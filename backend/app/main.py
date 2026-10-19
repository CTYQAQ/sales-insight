"""应用入口：生命周期、中间件、异常处理器、路由注册。"""
from contextlib import asynccontextmanager
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import bind_request_id, get_logger, setup_logging
from app.db.demo import ensure_demo_data
from app.db.seed import ensure_bootstrap
from app.db.session import init_db

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(json_format=settings.APP_ENV == "prod")
    settings.validate_startup()
    init_db()
    ensure_bootstrap()
    # 演示数据：表里已有数据就跳过，所以重启不会翻倍，生产关掉即可
    ensure_demo_data()
    log.info("%s started env=%s host=%s port=%s", settings.APP_NAME, settings.APP_ENV, settings.APP_HOST, settings.APP_PORT)
    yield
    from app.db.session import engine

    engine.dispose()
    log.info("shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        description="企业级销售智能分析平台 API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.APP_ENV != "prod" else None,
    )

    register_exception_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,  # refresh token 走 Cookie，必须允许凭证
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = bind_request_id(request.headers.get("X-Request-ID") or uuid.uuid4().hex)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001
            log.exception("request_failed path=%s", request.url.path)
            raise
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Response-Time"] = str(elapsed_ms)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        log.info("%s %s -> %d (%dms)", request.method, request.url.path, response.status_code, elapsed_ms)
        return response

    from app.modules.ai.router import router as ai_router
    from app.modules.alerts.router import router as alerts_router
    from app.modules.analytics.router import router as analytics_router
    from app.modules.auth.router import router as auth_router
    from app.modules.ingestion.router import router as ingestion_router
    from app.modules.settings.router import router as settings_router

    app.include_router(auth_router)
    app.include_router(ingestion_router)
    app.include_router(analytics_router)
    app.include_router(ai_router)
    app.include_router(alerts_router)
    app.include_router(settings_router)

    @app.get("/health", tags=["ops"])
    def health():
        return {"status": "ok", "env": settings.APP_ENV}

    @app.get("/ready", tags=["ops"])
    def ready():
        """就绪探针：真的查一次数据库，而不是无条件返回 ok。"""
        from sqlalchemy import text

        from app.db.session import SessionLocal

        try:
            with SessionLocal() as s:
                s.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            log.exception("readiness_check_failed")
            return JSONResponse(status_code=503, content={"status": "unavailable", "detail": str(exc)})
        return {"status": "ready", "llm_provider": settings.LLM_PROVIDER}

    return app


app = create_app()
