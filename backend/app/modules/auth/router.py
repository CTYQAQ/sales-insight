"""认证路由。

令牌策略：
- access token 走 JSON 返回，前端只放内存（刷新页面即失效，靠 refresh 静默续期）
- refresh token 写 httpOnly Cookie，JS 拿不到，XSS 偷不走
"""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response

from app.api.deps import UserDep, DbDep, require_roles
from app.core.config import settings
from app.core.errors import AppError, AuthError
from app.core.security import decode_token
from app.db.models.identity import Role, User
from app.modules.auth.schemas import LoginIn, TokenOut, UserCreate, UserOut, UserUpdate
from app.modules.auth.service import AuthService

router = APIRouter(prefix="/api/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"


def _set_refresh_cookie(response: Response, raw: str, expire: datetime) -> None:
    ttl = int((expire - datetime.utcnow()).total_seconds())
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=raw,
        httponly=True,
        samesite="lax",
        secure=settings.APP_ENV == "prod",
        max_age=max(ttl, 1),
        path="/api/auth",
    )


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, response: Response, session: DbDep):
    service = AuthService(session)
    user, access, refresh_raw, expire = service.authenticate(payload.username, payload.password)
    _set_refresh_cookie(response, refresh_raw, expire)
    return TokenOut(
        access_token=access,
        expires_in=settings.ACCESS_TOKEN_TTL_MIN * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/refresh", response_model=TokenOut)
def refresh(
    response: Response,
    session: DbDep,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
):
    if not refresh_token:
        raise AuthError("缺少刷新令牌")
    service = AuthService(session)
    try:
        access, new_raw, expire = service.rotate_refresh(refresh_token)
    except AppError:
        response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
        raise
    _set_refresh_cookie(response, new_raw, expire)
    claims = decode_token(access, expected_type="access")
    user = session.get(User, int(claims["sub"]))
    return TokenOut(
        access_token=access,
        expires_in=settings.ACCESS_TOKEN_TTL_MIN * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/logout")
def logout(
    response: Response,
    session: DbDep,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
):
    if refresh_token:
        AuthService(session).revoke_refresh(refresh_token)
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
    return {"message": "已退出登录"}


@router.get("/me", response_model=UserOut)
def me(user: UserDep, session: DbDep):
    entity = session.get(User, user.id)
    if entity is None:
        raise AuthError("账号不存在")
    return UserOut.model_validate(entity)


@router.get("/users", response_model=list[UserOut])
def list_users(session: DbDep, _: Annotated[object, Depends(require_roles(Role.ADMIN))]):
    return [UserOut.model_validate(u) for u in AuthService(session).list_users()]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    session: DbDep,
    actor: Annotated[object, Depends(require_roles(Role.ADMIN))],
):
    from app.api.deps import CurrentUser

    is_admin = isinstance(actor, CurrentUser) and actor.is_admin()
    user = AuthService(session).create_user(payload, actor_is_admin=is_admin)
    return UserOut.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    session: DbDep,
    _: Annotated[object, Depends(require_roles(Role.ADMIN))],
):
    return UserOut.model_validate(AuthService(session).update_user(user_id, payload))
