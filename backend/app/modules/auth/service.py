"""认证业务逻辑：登录、刷新轮换、用户管理。"""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AuthError, ConflictError, ForbiddenError, NotFoundError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.db.models.identity import RefreshToken, Role, User
from app.modules.auth.schemas import UserCreate, UserUpdate

log = get_logger(__name__)


class AuthService:
    def __init__(self, session: Session):
        self.session = session

    # ---------- 登录 ----------
    def authenticate(self, username: str, password: str) -> tuple[User, str, str, datetime]:
        user = self.session.execute(
            select(User).where(User.username == username)
        ).scalars().first()

        # 不区分「用户不存在」和「密码错误」，避免账号枚举
        if user is None or not verify_password(password, user.hashed_password):
            raise AuthError("用户名或密码错误")
        if not user.is_active:
            raise AuthError("账号已被停用")

        access = create_access_token(user.id, user.username, user.role.value, user.region_scope or [])
        refresh_raw, refresh_hash, expire = create_refresh_token(user.id)
        self.session.add(
            RefreshToken(user_id=user.id, token_hash=refresh_hash, expires_at=expire)
        )
        user.last_login_at = datetime.utcnow()
        self.session.commit()
        self.session.refresh(user)
        log.info("user_login user=%s role=%s", user.username, user.role.value)
        return user, access, refresh_raw, expire

    # ---------- 刷新（轮换，旧令牌立即失效）----------
    def rotate_refresh(self, raw: str) -> tuple[str, str, datetime]:
        token_hash = hash_refresh_token(raw)
        stored = self.session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        ).scalars().first()

        now = datetime.utcnow()
        if stored is None or stored.revoked or stored.expires_at < now:
            raise AuthError("刷新令牌无效或已过期")

        stored.revoked = True
        user = self.session.get(User, stored.user_id)
        if user is None or not user.is_active:
            raise AuthError("账号不存在或已停用")

        access = create_access_token(user.id, user.username, user.role.value, user.region_scope or [])
        new_raw, new_hash, expire = create_refresh_token(user.id)
        self.session.add(RefreshToken(user_id=user.id, token_hash=new_hash, expires_at=expire))
        self.session.commit()
        return access, new_raw, expire

    def revoke_refresh(self, raw: str) -> None:
        token_hash = hash_refresh_token(raw)
        stored = self.session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        ).scalars().first()
        if stored:
            stored.revoked = True
            self.session.commit()

    # ---------- 用户管理 ----------
    def list_users(self) -> list[User]:
        return self.session.execute(select(User).order_by(User.created_at.desc())).scalars().all()

    def create_user(self, payload: UserCreate, *, actor_is_admin: bool) -> User:
        # 只有管理员能建管理员
        if payload.role == Role.ADMIN and not actor_is_admin:
            raise ForbiddenError("只有管理员可以创建管理员账号")

        exists = self.session.execute(
            select(func.count(User.id)).where(User.username == payload.username)
        ).scalar()
        if exists:
            raise ConflictError(f"用户名 {payload.username} 已存在")

        user = User(
            username=payload.username,
            email=payload.email,
            hashed_password=hash_password(payload.password),
            role=payload.role,
            region_scope=payload.region_scope,
        )
        self.session.add(user)
        self.session.commit()
        self.session.refresh(user)
        log.info("user_created user=%s role=%s", user.username, user.role.value)
        return user

    def update_user(self, user_id: int, payload: UserUpdate) -> User:
        user = self.session.get(User, user_id)
        if user is None:
            raise NotFoundError("用户")

        for field in ("email", "role", "region_scope", "is_active"):
            value = getattr(payload, field, None)
            if value is not None:
                setattr(user, field, value)

        self.session.commit()
        self.session.refresh(user)
        return user
