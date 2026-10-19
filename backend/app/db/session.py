"""SQLAlchemy 2.0 会话与 Base。

SQLite 起步，切换 PostgreSQL 只需改 DATABASE_URL，业务代码不动。
"""
from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs() -> dict[str, Any]:
    if settings.is_sqlite:
        # SQLite 单文件：允许多线程共享同一连接
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10}


engine: Engine = create_engine(
    settings.DATABASE_URL, echo=settings.SQL_ECHO, future=True, **_engine_kwargs()
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


if settings.is_sqlite:

    @event.listens_for(Engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):  # noqa: ANN001, ANN202
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


def get_session() -> Generator[Session, None, None]:
    """FastAPI 依赖：每个请求一个会话，结束即关闭。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    """建表。生产环境请改用 Alembic 迁移，这里为快速启动提供便利。"""
    import app.db.models  # noqa: F401  保证所有模型被注册

    Base.metadata.create_all(bind=engine)
