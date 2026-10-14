import asyncio
import json
import logging
import re
from abc import (
    ABC,
    abstractmethod,
)
from collections.abc import (
    Awaitable,
    Callable,
)
from datetime import (
    datetime,
    timezone,
)
from email.utils import parsedate_to_datetime
from functools import cached_property
from math import isfinite
from types import TracebackType
from urllib.parse import (
    urlsplit,
    urlunsplit,
)

from httpx import (
    AsyncClient,
    Client,
    HTTPStatusError,
    Response,
    TimeoutException,
    TransportError,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)
from tenacity import RetryCallState

from baenggipyo.core.ratelimit import (
    RateLimiter,
    RateLimitPolicy,
)

from typing import (
    Annotated,
    ClassVar,
    Literal,
    TypeVar,
)


logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT: str = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
)

BODY_PREVIEW_LIMIT: int = 2048
RETRY_BODY_PREVIEW_LIMIT: int = 200
# 에러 본문은 에러 JSON 의 message 로 나간다. 인증정보와 함께 세션·추적 ID(sessionId, traceId, requestId, X-Request-Id 등)도 가린다
# (AGENTS.md: 세션 토큰·쿠키·추적 ID 는 출력하지 않는다)
SENSITIVE_KEYWORDS: str = (
    r'token|secret|password|passwd|api[-_]?key|access[-_]?key|'
    r'private[-_]?key|service[-_]?key|authorization|cookie|credential|'
    r'session|trace|span[-_]?id|request[-_]?id|correlation[-_]?id'
)
SENSITIVE_KEY_PATTERN: re.Pattern[str] = re.compile(f'({SENSITIVE_KEYWORDS})', re.IGNORECASE)
SENSITIVE_TEXT_PATTERN: re.Pattern[str] = re.compile(
    rf'([\w-]*(?:{SENSITIVE_KEYWORDS})[\w-]*\s*[=:]\s*)'
    r'([^,\s&}\n]+)',
    re.IGNORECASE,
)

_T = TypeVar('_T', bound='BaseHTTPClient')

# `close()` 가 이벤트 루프에 맡긴 async 클라이언트 닫기. 붙잡아 두지 않으면 태스크가 끝나기 전에 가비지 컬렉션될 수 있다
_closing_tasks: set[asyncio.Task[None]] = set()


def create_base_url(schema: Literal['https', 'http'], host: str) -> str:
    return f'{schema}://{host}'


def _redact_json(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: '<redacted>'
            if SENSITIVE_KEY_PATTERN.search(str(key))
            else _redact_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(value: str) -> str:
    return SENSITIVE_TEXT_PATTERN.sub(r'\1<redacted>', value)


def _safe_request_url(url: object) -> str:
    parts = urlsplit(str(url))
    netloc = parts.netloc.rsplit('@', 1)[-1]
    return urlunsplit((parts.scheme, netloc, parts.path, '', ''))


class ResponseStatusError(HTTPStatusError):
    """`HTTPStatusError` whose message carries a truncated response body."""


def _request_origin(exc: HTTPStatusError) -> str:
    """Name the request behind a status error without assuming one was attached.

    `HTTPStatusError.request` raises rather than returning None when the error
    was built without one, so the attribute is never read unguarded.
    """
    try:
        request = exc.request
    except RuntimeError:
        return '<unknown request>'
    return f'{request.method} {_safe_request_url(request.url)}'


def _body_preview(response: Response, limit: int = BODY_PREVIEW_LIMIT) -> str:
    """Render a response body short enough to sit inside an exception message.

    A body that was never read raises rather than returning text, so every
    failure here resolves to a marker instead of propagating.
    """
    try:
        body = response.text
    except Exception:
        return '<unread>'

    body = body.strip()
    if not body:
        return '<empty>'

    try:
        body = json.dumps(
            _redact_json(json.loads(body)),
            ensure_ascii=False,
        )
    except (json.JSONDecodeError, TypeError):
        body = _redact_text(body)

    if len(body) > limit:
        return f'{body[:limit]}... (truncated, {len(body)} chars)'
    return body


def raise_for_status(response: Response, limit: int = BODY_PREVIEW_LIMIT) -> None:
    """`Response.raise_for_status` that keeps the error body in the message.

    비공식 API 는 거절 사유를 본문에만 담아 보내므로 본문을 잘라 예외 메시지에
    붙인다. 헤더는 인증정보가 있으므로 넣지 않는다.

    Raises:
        ResponseStatusError: If the response carries a `4xx` or `5xx` status.
            It subclasses `HTTPStatusError`, so retry predicates that match on
            that type are unaffected.
    """
    try:
        response.raise_for_status()
    except HTTPStatusError as exc:
        raise ResponseStatusError(
            f'{_request_origin(exc)} -> '
            f'{response.status_code} {response.reason_phrase}\n'
            f'Response body: {_body_preview(response, limit)}',
            request=exc.request,
            response=exc.response,
        ) from None


def describe_failed_attempt(
    exc: BaseException | None,
    limit: int = RETRY_BODY_PREVIEW_LIMIT,
) -> str:
    """Render a failed attempt as one line for the retry log."""
    if exc is None:
        return '<no exception recorded>'
    if not isinstance(exc, HTTPStatusError):
        return f'{type(exc).__name__}: {exc}'

    body = ' '.join(_body_preview(exc.response, limit).split())
    return (
        f'{_request_origin(exc)} -> '
        f'{exc.response.status_code} {exc.response.reason_phrase} '
        f'| body: {body}'
    )


def should_retry_idempotent(exc: BaseException) -> bool:
    if isinstance(exc, HTTPStatusError):
        code = exc.response.status_code
        return code >= 500 or code == 429
    return isinstance(exc, (TimeoutException, TransportError))


def retry_after_seconds(header: str) -> float | None:
    """Read a `Retry-After` value in either form RFC 9110 allows.

    Args:
        header (str): Raw header value, as delay-seconds or an HTTP-date.

    Returns:
        float | None: Seconds to wait, or None when the value parses as
            neither form.
    """
    try:
        seconds = float(header)
    except ValueError:
        pass
    else:
        return seconds if isfinite(seconds) else None

    try:
        deadline = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return None
    if deadline is None:
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return (deadline - datetime.now(timezone.utc)).total_seconds()


def wait_retry_after(
    retry_state: RetryCallState,
    default: float = 1.0,
    maximum: float = 60.0,
) -> float:
    """Honour a rate-limit `Retry-After` header, capped at `maximum` seconds.

    Waits `default` seconds when the failure carries no usable header.
    """
    outcome = retry_state.outcome
    if outcome is None:
        return default

    exc = outcome.exception()
    if not isinstance(exc, HTTPStatusError):
        return default

    header = exc.response.headers.get('Retry-After')
    if not header:
        return default

    seconds = retry_after_seconds(header)
    if seconds is None:
        return default

    return min(max(seconds, 0.0), maximum)


def log_retry_before_sleep(retry_state: RetryCallState) -> None:
    outcome = retry_state.outcome
    next_action = retry_state.next_action
    if outcome is None or next_action is None:
        return

    logger.warning(
        'Attempt %s failed: %s | Retrying in %ss...',
        retry_state.attempt_number,
        describe_failed_attempt(outcome.exception()),
        next_action.sleep,
    )


class BaseHTTPClient(BaseModel, ABC):
    """Shared base model for the httpx-based provider clients.

    Defines the connection options every provider shares and a unified
    lifecycle for the sync/async httpx clients. Subclasses declare their own
    `host` and build `_client` / `_async_client` with ``@cached_property``.

    Args:
        url_schema: URL scheme used to build `base_url`. Defaults to 'https'.
        verify: TLS certificate verification flag. Defaults to True.
        proxy: Optional proxy URL. Defaults to None.
        timeout: Request timeout in seconds. Defaults to 20.
        user_agent: `User-Agent` header value. Defaults to a desktop Chrome UA.
        rate_limit: 프로세스 간 공유 요청 제한 정책. None 이면 클래스의
            `default_rate_limit` 을 쓴다. Defaults to None.

    Attributes:
        source_name: 요청 제한 상태 파일 이름 (서브클래스가 지정).
        default_rate_limit: 서브클래스 기본 제한 정책.
        rate_limiter: `source_name` 과 정책으로 만든 `RateLimiter`. 서브클래스는
            `rate_limit_hooks()` / `async_rate_limit_hooks()` 를 httpx 클라이언트의
            `event_hooks` 로 넘긴다.
    """

    source_name: ClassVar[str] = 'default'
    default_rate_limit: ClassVar[dict[str, object]] = {}

    model_config = ConfigDict(
        extra='forbid',
    )

    url_schema: Literal['http', 'https'] = 'https'

    verify: bool = True

    proxy: str | None = None

    timeout: Annotated[
        int,
        Field(gt=0)
    ] = 20

    user_agent: str = DEFAULT_USER_AGENT

    rate_limit: RateLimitPolicy | None = None

    @cached_property
    def rate_limiter(self) -> RateLimiter:
        policy = self.rate_limit or RateLimitPolicy(**self.default_rate_limit)
        return RateLimiter(self.source_name, policy)

    def rate_limit_hooks(self) -> dict[str, list[Callable[..., None]]]:
        return self.rate_limiter.event_hooks()

    def async_rate_limit_hooks(self) -> dict[str, list[Callable[..., Awaitable[None]]]]:
        # async 쪽(이 훅, `_async_client`, provider 의 `arun_request_*`)은 CLI 가 부르지 않지만 라이브러리로 쓸 때의 공개 API 라
        # 일부러 둔다 (provider-conventions.md '요청 메서드'). 호출부 0건은 죽은 코드가 아니다.
        return self.rate_limiter.async_event_hooks()

    @property
    @abstractmethod
    def _client(self) -> Client:
        """
        Synchronous HTTP client. Subclasses must override this with
        ``@cached_property`` so `close()` can locate it via
        ``__dict__.pop('_client', None)``. ``@cached_property`` cannot be
        combined with ``@abstractmethod``, so the abstract declaration stays a
        plain property.
        """
        ...

    @property
    @abstractmethod
    def _async_client(self) -> AsyncClient:
        """
        Asynchronous HTTP client. Subclasses must override this with
        ``@cached_property``; see ``_client``.
        """
        ...

    def close(self) -> None:
        """sync 클라이언트를 닫는다.

        `with` 안에서 async 메서드를 불러 async 클라이언트도 만들어졌으면, 실행 중인 이벤트 루프에 그 닫기를 맡긴다 (sync 함수라
        기다릴 수 없다). 루프가 이미 끝났으면 그 루프에서 연 연결은 다른 루프에서 닫을 수 없어 참조만 놓는다. async 메서드를 쓸 때는
        `async with` 나 `aclose()` 로 바로 닫는다.
        """
        client: Client | None = self.__dict__.pop('_client', None)
        if client is not None:
            client.close()
        async_client: AsyncClient | None = self.__dict__.pop('_async_client', None)
        if async_client is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(async_client.aclose())
        _closing_tasks.add(task)
        task.add_done_callback(_closing_tasks.discard)

    async def aclose(self) -> None:
        """
        Close the async client and, since a sync close is safe to call from
        async code, the sync client too.
        """
        async_client: AsyncClient | None = self.__dict__.pop('_async_client', None)
        if async_client is not None:
            await async_client.aclose()
        self.close()

    def __enter__(self: _T) -> _T:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None
    ) -> None:
        self.close()

    async def __aenter__(self: _T) -> _T:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None
    ) -> None:
        await self.aclose()

