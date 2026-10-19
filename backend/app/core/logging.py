"""结构化日志：每个请求一个 request_id，全链路可追。

绝不记录密码、令牌、API Key 等敏感信息。
"""
import contextvars
import json
import logging
import sys
import uuid

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_LEVEL = "DEBUG"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": _request_id.get(),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return f"{record.levelname:<7} [{_request_id.get()[:8]}] {record.getMessage()}"


def setup_logging(json_format: bool = False) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_format else PlainFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(_LEVEL)

    # 压掉第三方库的噪音
    for noisy in ("httpx", "httpcore", "openai", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def bind_request_id(value: str | None = None) -> str:
    rid = value or uuid.uuid4().hex
    _request_id.set(rid)
    return rid


def get_request_id() -> str:
    return _request_id.get()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
