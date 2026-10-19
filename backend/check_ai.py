"""AI 服务自查脚本 —— 401 报错时先跑这个。

用法（在 backend 目录下）：
    python check_ai.py

它只回答三个问题：
  1. .env 里的 Key 长相是否正常（长度 / 前后缀 / 有没有多余引号空格）
  2. 拿这个 Key 真打一次接口，看平台认不认
  3. 不通的话，最可能的原因是什么

所有输出都做脱敏处理，不会回显完整密钥。需要临时用别的 Key 试时不要改 .env，
直接设环境变量：DEEPSEEK_API_KEY=xxx python check_ai.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    print("缺少 python-dotenv，请先 pip install python-dotenv")
    raise SystemExit(1)


def mask(v: str) -> str:
    """只保留能用于比对的前后缀，中间一律打码。"""
    if not v:
        return "(空)"
    if len(v) <= 10:
        return "*" * len(v)
    return f"{v[:6]}{'*' * min(len(v) - 10, 20)}{v[-4:]}"


def load_local_env() -> Path | None:
    """优先 backend/.env（与本程序同级），找不到再向上找一级。"""
    here = Path(__file__).resolve().parent
    for candidate in (here / ".env", here.parent / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=True)
            return candidate
    return None


def diagnose(key: str, base_url: str, model: str) -> list[str]:
    """纯本地体检：不联网就能发现的低级错误。"""
    tips: list[str] = []
    if not key:
        return ["未读到 API Key：请在 .env 里配置 DEEPSEEK_API_KEY"]

    if key != key.strip():
        tips.append("Key 首尾有多余空白字符（复制时带进来的空格/换行），建议重新粘贴")

    quoted = len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'"
    if quoted:
        tips.append("Key 被引号包住了，形成 BaseSettings 会把它当字面值的一部分，请去掉引号")

    if any(c.isspace() for c in key.strip()):
        tips.append("Key 中间含有空白字符，很可能是复制时被折行截断了")

    # DeepSeek 官方签发的是 sk- + 36 位
    if "api.deepseek.com" in base_url:
        body = key[3:] if key.startswith("sk-") else key
        if not key.startswith("sk-"):
            tips.append(f"Key 不以 sk- 开头（实际前缀 {key[:4]!r}），大概率不是这个平台签发的")
        elif len(body) != 36:
            # 这只是个软提示：平台签发过不同长度的 Key，实测通得过就别管它。
            tips.append(
                f"提示（不一定有问题）：DeepSeek 常见 Key 是 sk- + 36 位，"
                f"当前 sk- 之后是 {len(body)} 位。若下方连通性测试通过即可忽略；"
                "若不通，请回控制台重新复制完整 Key，或确认它属于哪个平台"
                "（别家平台的 Key 要同步改 DEEPSEEK_BASE_URL）"
            )
    return tips


def main() -> int:
    env_file = load_local_env()
    print("=" * 60)
    print("AI 服务自查")
    print("=" * 60)
    print(f"读取配置: {env_file or '未找到 .env，只有环境变量'}")

    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").strip()
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip()
    provider = os.environ.get("LLM_PROVIDER", "deepseek").strip()

    print()
    print("--- 配置 ---")
    print(f"  LLM_PROVIDER     = {provider}")
    print(f"  DEEPSEEK_BASE_URL= {base_url}")
    print(f"  DEEPSEEK_MODEL   = {model}")
    print(f"  DEEPSEEK_API_KEY = {mask(key)}   (len={len(key)})")

    print()
    print("--- 本地体检 ---")
    tips = diagnose(key, base_url, model)
    if tips:
        for t in tips:
            print(f"  [!] {t}")
    else:
        print("  [ok] 格式检查通过")

    if provider == "ollama":
        print()
        print("LLM_PROVIDER=ollama，走本地模型，不走上面的 Key。")
        print(f"请确认 {os.environ.get('OLLAMA_BASE_URL', 'http://127.0.0.1:11434/v1')} 上的 Ollama 已启动。")
        return 0

    if not key:
        print()
        print("无法继续：没有 Key 可测。")
        return 1

    # ---------------- 真打一次接口 ----------------
    try:
        from openai import OpenAI
    except ImportError:
        print()
        print("缺少 openai 包，跳过连通性测试")
        return 1

    print()
    print("--- 连通性测试（发一条极短的消息）---")
    client = OpenAI(api_key=key, base_url=base_url, timeout=60)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "回复两个字：正常"}],
            max_tokens=10,
        )
        content = (resp.choices[0].message.content or "").strip()
        print(f"  [ok] 调用成功，模型返回：{content!r}")
        print()
        print("结论：Key 和接口地址都没问题，可以正常使用了。")
        return 0
    except Exception as exc:  # noqa: BLE001
        status = getattr(exc, "status_code", None)
        body = getattr(exc, "body", None) or {}
        msg = (body.get("error") or {}).get("message") if isinstance(body, dict) else None
        print(f"  [x] 调用失败 HTTP {status}")
        if msg:
            print(f"      {msg}")
        print()
        print("--- 排查建议 ---")
        if status in (401, 403):
            print("  服务端明确拒绝了鉴权，按以下顺序查：")
            print("  1. 去签发平台的控制台确认这个 Key 还在、没被删除")
            print("  2. 确认余额/额度没有耗尽（DeepSeek 余额不足也会返回 401）")
            print("  3. 确认 Key 和 DEEPSEEK_BASE_URL 是同一家 —— 各家 Key 不通用")
        elif status == 404:
            print(f"  接口或模型不存在：确认 base_url 是否需要带 /v1，以及模型名 {model!r} 是否正确")
        elif status == 429:
            print("  触发限流：稍后重试，或到控制台查看额度")
        elif status and status >= 500:
            print("  服务端故障：与本 Key 无关，稍后重试")
        else:
            print("  拿不到明确状态码，多半是网络问题（代理？防火墙？）")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
