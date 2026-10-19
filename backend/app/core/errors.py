"""类型化错误体系 + 全局处理器。

约定：业务层只抛 AppError 子类，全局处理器统一转成规范 JSON，
客户端永远看不到堆栈或内部细节。
"""
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, get_request_id

log = get_logger(__name__)


class AppError(Exception):
    """业务错误基类。"""

    status_code: int = 400
    code: str = "app_error"
    # True 时向客户端展示 message，否则只展示通用文案
    expose: bool = True

    def __init__(self, message: str = "请求处理失败", detail: Any = None):
        self.message = message
        self.detail = detail
        super().__init__(message)


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    def __init__(self, resource: str = "资源"):
        super().__init__(f"{resource}不存在")


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"
    def __init__(self, message: str = "登录已失效，请重新登录"):
        super().__init__(message)


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"
    def __init__(self, message: str = "没有该资源的访问权限"):
        super().__init__(message)


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class DependencyError(AppError):
    """外部依赖（LLM / 数据源 / 数据库）不可用。"""
    status_code = 503
    code = "dependency_unavailable"


class SqlSafetyError(AppError):
    """Text2SQL 安全拦截：生成了不允许的 SQL。"""
    status_code = 400
    code = "sql_blocked"
    def __init__(self, message: str = "生成的查询未通过安全校验"):
        super().__init__(message)


def _payload(code: str, message: str, request_id: str, detail: Any = None) -> dict:
    body = {"code": code, "message": message, "request_id": request_id}
    if detail is not None:
        body["detail"] = detail
    return body


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        msg = exc.message if exc.expose else "服务内部错误，请联系管理员"
        log.warning("app_error code=%s status=%s msg=%s", exc.code, exc.status_code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(exc.code, msg, get_request_id(), exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        fields = [
            {"field": ".".join(str(x) for x in e["loc"][1:]), "msg": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_payload("validation_error", "参数校验未通过", get_request_id(), fields),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload("http_error", str(exc.detail), get_request_id()),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled_exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content=_payload("internal_error", "服务开小差了，请稍后重试", get_request_id()),
        )
