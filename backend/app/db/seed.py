"""启动引导：确保有一个可用管理员账号，避免第一次进来卡在登录页。"""
from sqlalchemy import func, select

from app.core.logging import get_logger
from app.core.security import hash_password
from app.db.models.identity import Role, User
from app.db.session import SessionLocal

log = get_logger(__name__)

BOOTSTRAP_USERNAME = "admin"
BOOTSTRAP_PASSWORD = "admin123"


def ensure_bootstrap() -> None:
    with SessionLocal() as session:
        count = session.execute(select(func.count(User.id))).scalar() or 0
        if count > 0:
            return

        session.add(
            User(
                username=BOOTSTRAP_USERNAME,
                email="admin@example.local",
                hashed_password=hash_password(BOOTSTRAP_PASSWORD),
                role=Role.ADMIN,
                region_scope=[],
            )
        )
        session.commit()
        log.warning(
            "bootstrap_admin_created user=%s password=%s —— 请首次登录后立即改密码",
            BOOTSTRAP_USERNAME,
            BOOTSTRAP_PASSWORD,
        )
