"""运行时设置：读快照、改配置、测试连通性。

设计要点：
1. 配置的唯一来源仍是 .env / 环境变量（见 core/config.py），这里做的是
   「运行时覆盖」：改的是进程内那个 settings 单例的属性。所有消费方
   （LLM provider、embedding、检索开关、告警通知）都是**调用时**读
   settings.X，所以改完立即生效，不用重启。
2. 每次修改同时写回 .env，重启后不丢；.env 里其余行（注释、无关键）
   原样保留，只做行级替换 + 末尾追加。
3. 密钥出接口前一律脱敏（前 3 位 + 后 4 位），前端永远拿不到完整值；
   补丁里密钥字段传 None 表示不动，传空串表示显式清除。
"""
from pathlib import Path
import time
from typing import Any

from pydantic import BaseModel

from app.core.config import settings
from app.core.errors import DependencyError
from app.core.logging import get_logger

log = get_logger(__name__)


class SettingsPatch(BaseModel):
    """允许网页端修改的键白名单。None = 不动；密钥传 "" = 显式清除。"""

    # LLM
    LLM_PROVIDER: str | None = None
    DEEPSEEK_API_KEY: str | None = None
    DEEPSEEK_BASE_URL: str | None = None
    DEEPSEEK_MODEL: str | None = None
    OLLAMA_BASE_URL: str | None = None
    OLLAMA_MODEL: str | None = None
    # RAG / Embedding
    RAG_HYBRID: bool | None = None
    EMBEDDING_BACKEND: str | None = None
    EMBEDDING_MODEL: str | None = None
    EMBEDDING_DIM: int | None = None
    EMBEDDING_BASE_URL: str | None = None
    EMBEDDING_API_KEY: str | None = None
    # 告警通知
    WEBHOOK_TYPE: str | None = None
    WEBHOOK_URL: str | None = None
    WEBHOOK_KEYWORD: str | None = None
    SMTP_HOST: str | None = None
    SMTP_PORT: int | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    ALERT_EMAIL_TO: str | None = None


_ENUMS = {
    "LLM_PROVIDER": {"deepseek", "ollama"},
    "EMBEDDING_BACKEND": {"local", "bge", "api"},
    "WEBHOOK_TYPE": {"dingtalk", "wecom", "feishu", "none"},
}

# 改了必须重建索引的键 —— 集合名带模型指纹，混用新旧向量会得到噪声
_REBUILD_KEYS = {"EMBEDDING_BACKEND", "EMBEDDING_MODEL", "EMBEDDING_DIM"}

_SECRET_KEYS = {"DEEPSEEK_API_KEY", "EMBEDDING_API_KEY", "SMTP_PASSWORD", "WEBHOOK_URL"}


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••"
    return f"{value[:3]}…{value[-4:]}"


def snapshot() -> dict[str, Any]:
    """当前生效配置的全量快照（密钥脱敏）。前端设置页一次性拿这个渲染。"""
    s = settings
    return {
        "llm": {
            "provider": s.LLM_PROVIDER,
            "deepseek": {
                "api_key_set": bool(s.DEEPSEEK_API_KEY),
                "api_key_masked": _mask(s.DEEPSEEK_API_KEY),
                "base_url": s.DEEPSEEK_BASE_URL,
                "model": s.DEEPSEEK_MODEL,
            },
            "ollama": {"base_url": s.OLLAMA_BASE_URL, "model": s.OLLAMA_MODEL},
        },
        "rag": {
            "hybrid": s.RAG_HYBRID,
            "embedding_backend": s.EMBEDDING_BACKEND,
            "embedding_model": s.EMBEDDING_MODEL,
            "embedding_dim": s.EMBEDDING_DIM,
            "embedding_base_url": s.EMBEDDING_BASE_URL,
            "embedding_api_key_set": bool(s.EMBEDDING_API_KEY),
        },
        "notify": {
            "webhook_type": s.WEBHOOK_TYPE,
            "webhook_url_set": bool(s.WEBHOOK_URL),
            "webhook_url_masked": _mask(s.WEBHOOK_URL),
            "webhook_keyword": s.WEBHOOK_KEYWORD,
            "smtp_host": s.SMTP_HOST,
            "smtp_port": s.SMTP_PORT,
            "smtp_user": s.SMTP_USER,
            "smtp_password_set": bool(s.SMTP_PASSWORD),
            "email_to": s.ALERT_EMAIL_TO,
        },
        "system": {
            "app_name": s.APP_NAME,
            "app_env": s.APP_ENV,
            "database": "sqlite" if s.is_sqlite else "postgresql",
            "milvus_mode": "lite" if not s.MILVUS_CONN.startswith("http") else "standalone",
            "milvus_conn": s.MILVUS_CONN,
            "embedding_model_dir": s.EMBEDDING_MODEL_DIR,
            "text2sql_enabled": s.TEXT2SQL_ENABLED,
        },
    }


def _env_path() -> Path:
    """与 Settings.model_config.env_file 的查找顺序保持一致。"""
    for p in (Path(".env"), Path("../.env")):
        if p.exists():
            return p
    return Path(".env")


def _persist_env(updates: dict[str, Any]) -> None:
    """把变更写回 .env：已有行原位替换，新键追加到末尾，其余行原样保留。"""
    path = _env_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.strip().startswith("#") else None
        if key and key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    log.info("settings_persisted keys=%s path=%s", sorted(updates), path)


def apply_patch(patch: SettingsPatch) -> dict[str, Any]:
    updates = patch.model_dump(exclude_none=True)
    if not updates:
        return {"changed": [], "needs_rebuild": False}

    for key, value in updates.items():
        allowed = _ENUMS.get(key)
        if allowed and value not in allowed:
            raise DependencyError(f"{key} 取值非法：{value}（可选：{' / '.join(sorted(allowed))}）")

    for key, value in updates.items():
        setattr(settings, key, value)
    _persist_env(updates)

    changed = sorted(updates)
    log.info("settings_updated keys=%s", changed)
    return {
        "changed": changed,
        # 脱敏回显：告诉调用方密钥已落库，但不要把值再吐回去
        "masked": {k: _mask(str(v)) for k, v in updates.items() if k in _SECRET_KEYS},
        "needs_rebuild": bool(_REBUILD_KEYS & set(updates)),
    }


def test_llm() -> dict[str, Any]:
    """用当前生效配置真调一次最小对话，返回延迟。失败抛 DependencyError 给前端。"""
    from app.modules.ai.provider import get_llm_provider

    provider = get_llm_provider()
    started = time.perf_counter()
    reply = provider.complete(
        "你在做一次连通性自检。",
        "只回复两个字：正常",
        max_tokens=16,
        temperature=0,
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        "ok": True,
        "provider": provider.name,
        "model": settings.DEEPSEEK_MODEL if provider.name == "deepseek" else settings.OLLAMA_MODEL,
        "elapsed_ms": elapsed_ms,
        "reply": reply[:50],
    }


def test_notify(channel: str) -> dict[str, Any]:
    """真发一条测试消息。渠道没配置时明说，不假装成功。"""
    from app.modules.alerts import notifiers

    text = f"测试消息：告警通知渠道自检（{time.strftime('%Y-%m-%d %H:%M:%S')}）"
    if channel == "webhook":
        if settings.WEBHOOK_TYPE == "none" or not settings.WEBHOOK_URL:
            raise DependencyError("Webhook 未配置：先填写类型与地址再测试")
        notifiers.send_webhook(text)
        return {"ok": True, "channel": "webhook", "type": settings.WEBHOOK_TYPE}
    if channel == "email":
        if not all([settings.SMTP_HOST, settings.SMTP_USER, settings.ALERT_EMAIL_TO]):
            raise DependencyError("邮件未配置：SMTP 主机、账号、收件人缺一不可")
        notifiers.send_email("销售分析告警 · 测试", text)
        return {"ok": True, "channel": "email", "to": settings.ALERT_EMAIL_TO}
    raise DependencyError(f"未知渠道：{channel}（可选 webhook / email）")


def demo_reset() -> dict[str, Any]:
    """清空并重灌演示数据。危险操作，网页端有二次确认。"""
    from app.db.demo import ensure_demo_data

    written = ensure_demo_data(force=True)
    log.warning("demo_data_reset rows=%d", written)
    return {"ok": True, "rows_written": written}
