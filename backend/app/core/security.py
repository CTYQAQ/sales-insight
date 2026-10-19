"""认证与授权基础设施。

令牌策略：
- access token 短时效（默认 30 分钟），只放内存/请求头，不落 localStorage
- refresh token 服务端持久化，可吊销，轮换时旧令牌立即失效
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings
from app.core.errors import AuthError

_ALG = settings.JWT_ALGORITHM


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _now() -> datetime:
    # 全项目统一使用 naive UTC：SQLite 的 DateTime 不带时区，
    # 混用 aware/naive 会在比较时直接抛 TypeError
    return datetime.utcnow()


def create_access_token(user_id: str, username: str, role: str, region_scope: list[str]) -> str:
    expire = _now() + timedelta(minutes=settings.ACCESS_TOKEN_TTL_MIN)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "scope": region_scope,
        "type": "access",
        "jti": uuid.uuid4().hex,
        "exp": expire,
        "iat": _now(),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=_ALG)


def create_refresh_token(user_id: str) -> tuple[str, str, datetime]:
    """返回 (原始令牌, 仅用于存储的哈希, 过期时间)。库中只存哈希。"""
    raw = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expire = _now() + timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS)
    return raw, token_hash, expire


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def decode_token(token: str, *, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[_ALG])
    except jwt.ExpiredSignatureError:
        raise AuthError("令牌已过期")
    except jwt.InvalidTokenError:
        raise AuthError("令牌无效")
    if payload.get("type") != expected_type:
        raise AuthError("令牌类型不匹配")
    return payload
