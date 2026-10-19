"""LLM 供应商抽象层。

设计要点：
1. 所有返回必须是结构化或抛异常，**绝不把错误字符串当内容返回**——
   这是很多 AI 应用最隐蔽的缺陷：上游失败会伪装成一份正常报告流向下游。
2. 换模型只加一个 Provider 实现类，业务代码不动。
"""
import time
from abc import ABC, abstractmethod

from openai import (
    APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError,
    BadRequestError, InternalServerError, OpenAI, PermissionDeniedError,
    RateLimitError,
)

from app.core.config import settings
from app.core.errors import DependencyError
from app.core.logging import get_logger

log = get_logger(__name__)


class LLMProvider(ABC):
    name: str

    # 只有这些值得重试：网络抖动、超时、限流、服务端自身故障，都是「过一会儿可能就好了」。
    # 鉴权失败(401)、权限不足(403)、参数有误(400) 属于确定性错误 —— 重试一万次结果完全一样，
    # 退避只会让用户在错误的路上白白多等几秒，所以一律立刻失败。
    RETRYABLE = (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)

    @abstractmethod
    def complete(
        self, system: str, user: str, *, max_tokens: int = 2000, temperature: float = 0.3
    ) -> str: ...

    def auth_hint(self) -> str:
        """鉴权失败时怎么自查。子类按各自平台给出具体指引。"""
        return "请检查 AI 服务配置是否正确"

    @staticmethod
    def _brief(exc: Exception) -> str:
        """把上游异常压成一句人话，避免把 request_id 之类的内部细节甩给用户。"""
        status = getattr(exc, "status_code", None)
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            msg = (body.get("error") or {}).get("message")
            if isinstance(msg, str):
                return f"HTTP {status}：{msg}" if status else msg
        return f"HTTP {status}" if status else type(exc).__name__

    def _retry(self, fn, *, tries: int | None = None):
        """指数退避。只重试可恢复错误，业务错误立刻抛并选择能照着做的提示。"""
        attempts = tries or (settings.LLM_MAX_RETRIES + 1)
        last: Exception | None = None

        for i in range(attempts):
            try:
                return fn()
            except self.RETRYABLE as exc:
                last = exc
                if i == attempts - 1:
                    break
                sleep_s = 2**i
                log.warning("llm_call_retry attempt=%d/%d err=%s", i + 1, attempts, exc)
                time.sleep(sleep_s)
            except AuthenticationError as exc:
                # 这类报错以前会被当成普通异常重试 3 次，用户要多等 6 秒才收到一句看不懂的 JSON。
                log.error("llm_auth_failed provider=%s detail=%s", self.name, self._brief(exc))
                raise DependencyError(f"AI 服务鉴权失败（401）：{self.auth_hint()}") from exc
            except PermissionDeniedError as exc:
                log.error("llm_permission_denied provider=%s detail=%s", self.name, self._brief(exc))
                raise DependencyError("AI 服务拒绝访问（403）：该密钥没有调用此模型的权限") from exc
            except BadRequestError as exc:
                log.error("llm_bad_request provider=%s detail=%s", self.name, self._brief(exc))
                raise DependencyError(f"AI 服务拒绝了本次请求：{self._brief(exc)}") from exc
            except APIStatusError as exc:
                # 其余 4xx：同样不具备重试价值
                log.error("llm_rejected provider=%s detail=%s", self.name, self._brief(exc))
                raise DependencyError(f"AI 服务调用失败：{self._brief(exc)}") from exc

        raise DependencyError(f"AI 服务调用失败：{self._brief(last)}")


class DeepSeekProvider(LLMProvider):
    name = "deepseek"

    def __init__(self) -> None:
        if not settings.DEEPSEEK_API_KEY:
            raise DependencyError("未配置 DEEPSEEK_API_KEY")
        self._client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
            timeout=settings.LLM_TIMEOUT_SEC,
        )
        self._model = settings.DEEPSEEK_MODEL

    def auth_hint(self) -> str:
        """401 时最可能的三个原因，按排查成本从低到高排。"""
        return (
            "API Key 无效或与接口地址不匹配。请依次核对："
            f"1) backend/.env 里的 DEEPSEEK_API_KEY 是否复制完整（当前 base_url="
            f"{settings.DEEPSEEK_BASE_URL}）；"
            "2) 该 Key 是否是这个平台签发的 —— 不同厂商的 Key 不通用；"
            "3) 余额是否耗尽或 Key 是否已被删除。"
            "可用 `python check_ai.py` 自查"
        )

    def complete(self, system: str, user: str, *, max_tokens: int = 2000, temperature: float = 0.3) -> str:
        def _call():
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            content = resp.choices[0].message.content
            if not content or not content.strip():
                raise DependencyError("AI 返回空内容")
            return content.strip()

        return self._retry(_call)


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self) -> None:
        self._client = OpenAI(
            api_key="ollama",
            base_url=settings.OLLAMA_BASE_URL,
            timeout=settings.LLM_TIMEOUT_SEC,
        )
        self._model = settings.OLLAMA_MODEL

    def auth_hint(self) -> str:
        return f"本地 Ollama 需要鉴权：请检查 {settings.OLLAMA_BASE_URL} 上的服务配置"

    def complete(self, system: str, user: str, *, max_tokens: int = 2000, temperature: float = 0.3) -> str:
        def _call():
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            content = resp.choices[0].message.content
            if not content or not content.strip():
                raise DependencyError("本地模型返回空内容")
            return content.strip()

        return self._retry(_call)


def get_llm_provider() -> LLMProvider:
    if settings.LLM_PROVIDER == "ollama":
        return OllamaProvider()
    return DeepSeekProvider()
