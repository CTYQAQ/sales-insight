"""端到端验证：登录 → 灌知识库 → 三种意图 → 多轮追问 → 检索对比。

单进程跑完，不依赖外部脚本分步执行。
用法：python verify_agent.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8124"
OUT = Path(__file__).parent / "verify_agent_out.txt"
lines: list[str] = []


def p(*a):
    s = " ".join(str(x) for x in a)
    lines.append(s)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(s, flush=True)


def req(method: str, path: str, *, token: str = "", body: dict | None = None, timeout: int = 90):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")[:400]
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def ask(token: str, q: str, history: list[dict] | None = None) -> dict:
    code, data = req("POST", "/api/chat/dialog", token=token, body={"question": q, "history": history or []})
    if code != 200 or not isinstance(data, dict):
        return {"_error": f"HTTP {code}: {data}"}
    return data


def main() -> int:
    p("=== 1. 健康检查 ===")
    code, data = req("GET", "/health")
    p(f"health: {code} {data}")
    if code != 200:
        p("后端未就绪，退出")
        return 1

    p("\n=== 2. 登录 ===")
    code, data = req("POST", "/api/auth/login", body={"username": "admin", "password": "admin123"})
    if code != 200:
        p(f"登录失败 {code}: {data}")
        return 1
    token = data["access_token"]
    p(f"登录成功，角色={data['user']['role']}，可见区域={data['user']['region_scope'] or '全部'}")

    p("\n=== 3. 灌入内置知识库 ===")
    code, data = req("POST", "/api/knowledge/seed", token=token)
    p(f"seed: {code} {json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else data}")
    code, st = req("GET", "/api/knowledge/stats", token=token)
    p(f"stats: {json.dumps(st, ensure_ascii=False)}")

    p("\n=== 4. 三种意图各测一次 ===")
    cases = [
        ("毛利率怎么算", "knowledge"),
        ("华东区上个月营收多少", "data"),
        ("你好，你能做什么", "chat"),
    ]
    for q, expect in cases:
        d = ask(token, q)
        if "_error" in d:
            p(f"  [FAIL] {q} -> {d['_error']}")
            continue
        ok = "OK  " if d["intent"] == expect else "MISS"
        p(f"\n  [{ok}] {q}")
        p(f"        意图：{d['intent']}（期望 {expect}）· {d['intent_reason']}")
        p(f"        耗时：{d['elapsed_ms']} ms")
        p(f"        回答：{d['answer'][:180].replace(chr(10), ' ')}")
        if d.get("sources"):
            p(f"        来源：{[s['title'] for s in d['sources']]}")
        if d.get("sql"):
            p(f"        SQL：{d['sql'][:150]}")
        p("        轨迹：" + " → ".join(f"{t['node']}({t['elapsed_ms']}ms)" for t in d["trace"]))

    p("\n=== 5. 多轮追问（指代消解）===")
    d1 = ask(token, "华东区上个月营收多少")
    p(f"  第1轮：{d1.get('answer', '')[:100]}")
    hist = [
        {"role": "user", "content": "华东区上个月营收多少"},
        {"role": "assistant", "content": d1.get("answer", "")},
    ]
    d2 = ask(token, "那华南呢", hist)
    p(f"  第2轮问：那华南呢")
    p(f"    上下文理解为：{d2.get('standalone_question')}")
    p(f"    意图：{d2.get('intent')} · 耗时 {d2.get('elapsed_ms')} ms")
    p(f"    回答：{d2.get('answer', '')[:160]}")

    p("\n=== 6. 混合检索 vs 纯向量 ===")
    probes = ["华中区有没有江西", "报表和财务对不上", "换模型后检索变空了", "退货金额在什么月份冲减营收"]
    for q in probes:
        code, hyb = req("GET", f"/api/knowledge/search?q={urllib.parse.quote(q)}&top_k=2&hybrid=true", token=token)
        code2, vec = req("GET", f"/api/knowledge/search?q={urllib.parse.quote(q)}&top_k=2&hybrid=false", token=token)
        h = [x["title"] for x in hyb] if isinstance(hyb, list) else hyb
        v = [x["title"] for x in vec] if isinstance(vec, list) else vec
        same = "一致" if h == v else "不同"
        p(f"\n  {q}")
        p(f"    混合  ：{h}")
        p(f"    纯向量：{v}  -> {same}")

    p("\n=== 7. 异常路径：知识库无关问题 ===")
    d = ask(token, "公司食堂几点开饭")
    p(f"  意图：{d.get('intent')} · 来源数：{len(d.get('sources', []))}")
    p(f"  回答：{d.get('answer', '')[:150]}")

    p("\n=== ALL DONE ===")
    return 0


if __name__ == "__main__":
    import urllib.parse  # noqa: F401  （供 f-string 里的 quote 使用）

    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        import traceback

        p(f"FATAL {type(exc).__name__}: {exc}")
        p(traceback.format_exc())
        sys.exit(1)
