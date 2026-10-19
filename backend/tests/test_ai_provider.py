"""AI provider 重试策略回归测试。

背景：_retry 曾经用 `except Exception` 无差别重试 —— 于是 401 鉴权失败也会被
退避重试 3 次，每次让用户干等 3 秒多，最后还把上游原始 JSON 整个透传出去。
这个文件的目的就是把修好的行为钉住，防止以后有人手滑改回去。
"""
import time

import httpx
import pytest
from openai import (
    APIStatusError, APITimeoutError, AuthenticationError,
    BadRequestError, InternalServerError, RateLimitError,
)

from app.core.errors import DependencyError
from app.modules.ai.provider import DeepSeekProvider, LLMProvider


class DummyProvider(LLMProvider):
    """不需要联网的最小实现，专门用来观察 _retry 的行为。"""

    name = "dummy"

    def complete(self, system: str, user: str, **kwargs) -> str:  # pragma: no cover - 用不到
        return ""


def _exc(cls, status: int, message: str = "boom"):
    return cls(
        message,
        response=httpx.Response(status, request=httpx.Request("POST", "https://example.test")),
        body={"error": {"message": message}},
    )


@pytest.fixture
def provider() -> DummyProvider:
    return DummyProvider()


def test_auth_failure_fails_fast_without_retry(provider: DummyProvider) -> None:
    """401 是确定性错误，重试一万次结果也一样 —— 必须立刻失败。"""
    calls: list[int] = []

    def always_401() -> None:
        calls.append(1)
        raise _exc(AuthenticationError, 401, "api key invalid (request_id: abc-123)")

    started = time.perf_counter()
    with pytest.raises(DependencyError) as exc:
        provider._retry(always_401)
    elapsed = time.perf_counter() - started

    assert len(calls) == 1, "401 不应该被重试"
    assert elapsed < 0.5, f"401 必须快速失败，实际耗时 {elapsed:.2f}s"


def test_auth_error_message_is_actionable_and_leaks_no_internals(
    provider: DummyProvider,
) -> None:
    """给用户的提示要能照着做，且不能含 request_id 这类内部细节。"""
    with pytest.raises(DependencyError) as exc:
        provider._retry(lambda: (_ for _ in ()).throw(_exc(AuthenticationError, 401, "invalid")))

    message = exc.value.message
    assert "api key" in message.lower() or "401" in message
    # 内部细节不该出现给用户
    assert "request_id" not in message
    assert "{" not in message


def test_retryable_errors_are_retried_with_backoff(provider: DummyProvider) -> None:
    """网络/限流/服务端错误属于「过一会儿可能就好了」，应当重试。"""
    calls: list[int] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise _exc(InternalServerError, 500)
        return "ok"

    result = provider._retry(flaky)

    assert result == "ok"
    assert len(calls) == 3


@pytest.mark.parametrize("cls", [APITimeoutError, RateLimitError, InternalServerError])
def test_all_retryable_types_are_registered(cls) -> None:
    assert issubclass(cls, LLMProvider.RETRYABLE)


@pytest.mark.parametrize("cls", [AuthenticationError, BadRequestError])
def test_auth_and_badrequest_are_not_retried(cls) -> None:
    assert not issubclass(cls, LLMProvider.RETRYABLE)


def test_bad_request_is_not_retried(provider: DummyProvider) -> None:
    calls: list[int] = []

    def bad() -> None:
        calls.append(1)
        raise _exc(BadRequestError, 400, "invalid temperature")

    with pytest.raises(DependencyError) as exc:
        provider._retry(bad)

    assert len(calls) == 1
    assert "400" in exc.value.message


def test_exhausted_retries_surface_last_error(provider: DummyProvider) -> None:
    """重试耗尽后要给出可读的错误，而不是把 None 或裸异常抛出去。"""
    with pytest.raises(DependencyError) as exc:
        provider._retry(lambda: (_ for _ in ()).throw(_exc(InternalServerError, 500)))

    assert "AI 服务调用失败" in exc.value.message
    assert "None" not in exc.value.message


def test_deepseek_auth_hint_points_to_env_and_script() -> None:
    """排查指引要具体：告诉人去改哪个文件、跑哪个命令。"""
    hint = DeepSeekProvider.auth_hint(DeepSeekProvider.__new__(DeepSeekProvider))

    assert ".env" in hint
    assert "check_ai.py" in hint
    assert "DEEPSEEK_API_KEY" in hint


def test_unknown_api_status_error_still_fails_fast(provider: DummyProvider) -> None:
    """落在白名单外的其他 4xx 同样不具备重试价值。"""
    calls: list[int] = []

    def weird() -> None:
        calls.append(1)
        raise _exc(APIStatusError, 418, "I'm a teapot")

    with pytest.raises(DependencyError):
        provider._retry(weird)

    assert len(calls) == 1
