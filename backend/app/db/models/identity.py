"""身份模型：用户、角色、刷新令牌。

区域级行权限靠 user.region_scope 实现：空列表 = 全区域可见。
"""
import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Role(str, enum.Enum):
    ADMIN = "admin"        # 全部权限 + 用户与数据源管理
    ANALYST = "analyst"    # 看数 + 生成报告 + 配置告警
    VIEWER = "viewer"      # 只看属于自己的区域


class User(Base):
    __tablename__ = "user"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(128))
    hashed_password: Mapped[str] = mapped_column(String(128))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.VIEWER, index=True)
    # 行级数据范围：["east","south"] 表示只能看这些区域；[] 表示全部
    region_scope: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def can_see_all_regions(self) -> bool:
        return not self.region_scope

    def visible_regions(self) -> list[str] | None:
        """返回 None 表示不限制，否则返回允许的区域码列表。"""
        return None if self.can_see_all_regions() else list(self.region_scope)


class RefreshToken(Base):
    __tablename__ = "refresh_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
