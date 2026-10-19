"""依赖注入：数据库会话、当前用户、角色校验、区域范围过滤。"""
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import AuthError, ForbiddenError
from app.core.logging import get_logger
from app.core.security import decode_token
from app.db.models.identity import Role, User
from app.db.session import get_session

log = get_logger(__name__)

bearer = HTTPBearer(auto_error=False)

DbDep = Annotated[Session, Depends(get_session)]


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    role: Role
    region_scope: list[str]

    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    def is_analyst(self) -> bool:
        return self.role in (Role.ADMIN, Role.ANALYST)

    def visible_regions(self) -> list[str] | None:
        return None if not self.region_scope else list(self.region_scope)


def _load_user(session: Session, payload: dict) -> User:
    user = session.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthError("账号不存在或已停用")
    return user


def get_current_user(
    session: DbDep,
    cred: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> CurrentUser:
    if cred is None:
        raise AuthError("缺少认证令牌")
    payload = decode_token(cred.credentials, expected_type="access")
    user = _load_user(session, payload)
    return CurrentUser(
        id=user.id,
        username=user.username,
        role=user.role,
        region_scope=list(user.region_scope or []),
    )


UserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_roles(*allowed: Role):
    """角色校验工厂。用法：deps=Depends(require_roles(Role.ADMIN))"""

    def _check(user: UserDep) -> CurrentUser:
        if user.role not in allowed:
            raise ForbiddenError("当前角色无权执行该操作")
        return user

    return _check


def apply_region_scope(query, column, user: CurrentUser):
    """把行级数据范围套到查询上。超管与 Analyst 看全量，Viewer 只看授权区域。"""
    regions = user.visible_regions()
    if regions is None:
        return query
    return query.where(column.in_(regions))
