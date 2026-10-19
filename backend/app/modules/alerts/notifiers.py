"""告警通知渠道：钉钉 / 企业微信 / 飞书 / 邮件。

任何一个渠道失败都不能影响告警入库，所以这里吞异常只记日志。
"""
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


def dispatch_notification(message: str, channels: list[str] | None = None) -> list[str]:
    if not channels:
        return []
    sent: list[str] = []
    for ch in channels:
        try:
            if ch == "webhook":
                send_webhook(message)
                sent.append("webhook")
            elif ch == "email":
                send_email("销售指标告警", message)
                sent.append("email")
        except Exception:  # noqa: BLE001
            log.exception("notify_failed channel=%s", ch)
    return sent


def _prefixed(message: str) -> str:
    kw = settings.WEBHOOK_KEYWORD
    return f"{kw}告警\n{message}" if kw else message


def _payload(message: str) -> dict[str, Any]:
    """按渠道类型组不同的请求体。"""
    if settings.WEBHOOK_TYPE == "wecom":
        return {"msgtype": "text", "text": {"content": _prefixed(message)}}
    if settings.WEBHOOK_TYPE == "feishu":
        return {"msg_type": "text", "content": {"text": _prefixed(message)}}
    return {"msgtype": "text", "text": {"content": _prefixed(message)}}


def send_webhook(message: str) -> None:
    if settings.WEBHOOK_TYPE == "none" or not settings.WEBHOOK_URL:
        log.info("webhook_not_configured skip")
        return

    import httpx

    resp = httpx.post(settings.WEBHOOK_URL, json=_payload(message), timeout=10)
    resp.raise_for_status()
    log.info("webhook_sent type=%s", settings.WEBHOOK_TYPE)


def send_email(subject: str, body: str) -> None:
    if not all([settings.SMTP_HOST, settings.SMTP_USER, settings.ALERT_EMAIL_TO]):
        log.info("smtp_not_configured skip")
        return

    import smtplib
    from email.header import Header
    from email.mime.text import MIMEText

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = settings.SMTP_USER
    msg["To"] = settings.ALERT_EMAIL_TO

    if settings.SMTP_PORT == 465:
        server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10)
    else:
        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10)
        server.starttls()

    with server:
        if settings.SMTP_PASSWORD:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.sendmail(settings.SMTP_USER, settings.ALERT_EMAIL_TO.split(","), msg.as_string())
    log.info("email_sent to=%s", settings.ALERT_EMAIL_TO)
