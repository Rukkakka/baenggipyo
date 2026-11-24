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
    NetworkError,
    RemoteProtocolError,
    Request,
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

from baenggipyo.core.browser import (
    DISCARD_STATE_STATUSES,
    READY_COOKIE_TIMEOUT_SECONDS,
    AsyncBrowserTransport,
    BrowserOptions,
    BrowserTransport,
)
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

BODY_PREVIEW_LIMIT: int = 2048
RETRY_BODY_PREVIEW_LIMIT: int = 200
# 재시도 대기 상한 (초). `Retry-After` 가 이보다 길어도 이만큼만 기다린다
RETRY_MAX_WAIT_SECONDS: float = 60.0
# 다시 보내면 나을 수 있는 전송 오류: 시간 초과, 연결·읽기·쓰기 실패, 서버가 응답 중 끊음. 프록시 거절(ProxyError)·
# 지원하지 않는 URL(UnsupportedProtocol)·요청 형식 오류(LocalProtocolError)처럼 다시 보내도 같은 TransportError 는 빼서
# 재시도 대기와 요청 제한 슬롯을 쓰지 않는다. 브라우저 transport 를 쓰는 클라이언트의 재시도 술어가 이것을 쓴다
RETRYABLE_TRANSPORT_ERRORS: tuple[type[TransportError], ...] = (TimeoutException, NetworkError, RemoteProtocolError)

# 경로에 세션 ID 같은 응답 값을 넣는 요청은 이 요청 확장(`extensions`) 키로 자리표시자를 남긴 엔드포인트(`/…/{session_id}`)를 넘긴다.
# 에러 메시지·재시도 로그·요청 로그는 실제 경로 대신 이것을 쓴다 (본문 가리기는 URL 에 닿지 않는다)
LOG_PATH_EXTENSION: str = 'baenggipyo_log_path'

# 에러 본문은 에러 JSON 의 message 로 나간다. 인증정보와 함께 세션·추적 ID(sessionId, traceId, requestId, X-Request-Id 등)도 가린다
# (AGENTS.md: 세션 토큰·쿠키·추적 ID 는 출력하지 않는다). 스카이스캐너 봇 차단 본문의 `redirect_to` 는 캡차 주소이고 쿼리에 추적 ID(uuid, vid)와
# 요청 경로(url, base64 라 폴링이면 세션 ID 가 든다)가 있어 통째로 가린다. vid 는 낱말 끝일 때만 본다 (provider 같은 키를 가리지 않게)
SENSITIVE_KEYWORDS: str = (
    r'token|secret|password|passwd|api[-_]?key|access[-_]?key|'
    r'private[-_]?key|service[-_]?key|authorization|cookie|credential|'
    r'session|trace|span[-_]?id|request[-_]?id|correlation[-_]?id|'
    r'uuid|vid\b|redirect[-_]?to'
)
SENSITIVE_KEY_PATTERN: re.Pattern[str] = re.compile(f'({SENSITIVE_KEYWORDS})', re.IGNORECASE)
# JSON 으로 읽히지 않는 본문(HTML 안의 JSON, 잘린 JSON)과 JSON 값 안에 든 JSON 문자열은 키·값이 따옴표에 싸여 온다. 따옴표를 건너뛰지 않으면
# `"sessionId": "…"` 를 가리지 못한다
SENSITIVE_TEXT_PATTERN: re.Pattern[str] = re.compile(
    rf'([\w-]*(?:{SENSITIVE_KEYWORDS})[\w-]*["\']?\s*[=:]\s*["\']?)'
    r'([^"\',\s&}\n]+)',
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


def _safe_request_url(url: object, path: str | None = None) -> str:
    parts = urlsplit(str(url))
    netloc = parts.netloc.rsplit('@', 1)[-1]
    return urlunsplit((parts.scheme, netloc, parts.path if path is None else path, '', ''))


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
    return _describe_request(request)


def _describe_request(request: Request) -> str:
    """로그·에러 메시지에 쓰는 요청 이름: 메서드와 쿼리·계정 정보를 뗀 주소 (`LOG_PATH_EXTENSION` 이 있으면 그 경로)."""
    log_path = request.extensions.get(LOG_PATH_EXTENSION)
    return f'{request.method} {_safe_request_url(request.url, log_path if isinstance(log_path, str) else None)}'


def _log_response(response: Response) -> None:
    # httpx 의 요청 로그(INFO)는 쿼리와 경로의 세션 ID 를 그대로 찍어 CLI 가 끈다 (`configure_logging`). 같은 줄을 가린 주소로 남긴다
    logger.info('%s -> %d %s', _describe_request(response.request), response.status_code, response.reason_phrase)


async def _alog_response(response: Response) -> None:
    _log_response(response)


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
    # 모든 클라이언트가 브라우저로 보내, 다시 보내도 같은 브라우저 오류(CORS·프록시·인증서)는 재시도하지 않는다
    return isinstance(exc, RETRYABLE_TRANSPORT_ERRORS)


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
    maximum: float = RETRY_MAX_WAIT_SECONDS,
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
        rate_limit: 프로세스 간 공유 요청 제한 정책. None 이면 클래스의
            `default_rate_limit` 을 쓴다. Defaults to None.

    Attributes:
        source_name: 요청 제한 상태 파일 이름 (서브클래스가 지정).
        default_rate_limit: 서브클래스 기본 제한 정책.
        rate_limiter: `source_name` 과 정책으로 만든 `RateLimiter`. 서브클래스는
            `rate_limit_hooks()` / `async_rate_limit_hooks()` 를 httpx 클라이언트의
            `event_hooks` 로 넘긴다.
        browser_entry_url: 브라우저가 처음 열 사이트 화면 (서브클래스가 지정). 브라우저로 요청하는 서브클래스는
            `browser_transport()` / `async_browser_transport()` 를 httpx 클라이언트의 `transport` 로 넘긴다.
        browser_ready_cookie: 첫 화면을 연 뒤 생길 때까지 기다리고, 남긴 쿠키를 쓸 수 있는지 볼 쿠키 이름.
        browser_api_hosts: 첫 화면이 보낸 요청을 요청 제한에 셀 사이트 API 호스트. 비면 `browser_entry_url` 의 호스트.
            호스트 필드에서 만드는 property 라, 호스트 필드를 바꿔도 맞는다 (서브클래스가 덮어쓴다). 요청은 첫 화면 탭에서
            보내므로 호스트를 다른 출처로 바꾸면 그 서버가 첫 화면 출처의 CORS 를 허용해야 한다.
        browser_discard_statuses: 우리 요청이 받으면 남긴 쿠키를 버릴 HTTP 상태.
        browser_fetch_credentials: 브라우저 fetch 의 `credentials`. 'include' 면 첫 화면과 다른 출처 API 에도 쿠키를 싣는다
            (`BrowserOptions.fetch_credentials`).
        browser_keep_cookies: 쿠키를 파일에 남기고 다음 명령이 이어 쓴다 (`BrowserOptions.keep_cookies`).
        browser_persistent_profile: 브라우저 기록 폴더(`<state_dir>/browser/<source>-profile`)를 통째로 이어 쓴다
            (`BrowserOptions.persistent_profile`).
        browser_stay_on_entry_page: 첫 화면을 빈 페이지로 바꾸지 않고 그 탭에서 요청한다 (`BrowserOptions.stay_on_entry_page`).
        browser_count_entry_requests: 첫 화면이 스스로 보낸 요청을 요청 제한에 센다 (`BrowserOptions.count_entry_requests`).
        browser_block_entry_resources: 첫 화면을 여는 동안 정적 파일을 막는다 (`BrowserOptions.block_entry_resources`).
        browser_ready_timeout: `browser_ready_cookie` 를 기다리는 시간 상한(초) (`BrowserOptions.ready_timeout`).
        browser_entry_settle_seconds: 새로 연 첫 화면에서 쿠키 준비 뒤 더 기다리는 시간(초) (`BrowserOptions.entry_settle_seconds`).
        browser_entry_block_cooldown: 첫 화면 문서가 401·403·451 로 막히면 요청 제한에 차단 쿨다운을 건다 (`BrowserOptions.entry_block_cooldown`).
    """

    source_name: ClassVar[str] = 'default'
    default_rate_limit: ClassVar[dict[str, object]] = {}
    browser_entry_url: ClassVar[str] = ''
    browser_ready_cookie: ClassVar[str | None] = None
    browser_discard_statuses: ClassVar[frozenset[int]] = DISCARD_STATE_STATUSES
    browser_fetch_credentials: ClassVar[Literal['same-origin', 'include']] = 'same-origin'
    browser_keep_cookies: ClassVar[bool] = True
    browser_persistent_profile: ClassVar[bool] = False
    browser_stay_on_entry_page: ClassVar[bool] = False
    browser_count_entry_requests: ClassVar[bool] = True
    browser_block_entry_resources: ClassVar[bool] = True
    browser_ready_timeout: ClassVar[float] = READY_COOKIE_TIMEOUT_SECONDS
    browser_entry_settle_seconds: ClassVar[float] = 0.0
    browser_entry_block_cooldown: ClassVar[bool] = False

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

    rate_limit: RateLimitPolicy | None = None

    @cached_property
    def rate_limiter(self) -> RateLimiter:
        policy = self.rate_limit or RateLimitPolicy(**self.default_rate_limit)
        return RateLimiter(self.source_name, policy)

    def rate_limit_hooks(self) -> dict[str, list[Callable[..., None]]]:
        hooks = self.rate_limiter.event_hooks()
        # 요청 로그를 먼저 남긴다. 429·봇 차단이면 요청 제한 훅이 예외를 던져 뒤 훅은 돌지 않는다
        hooks['response'].insert(0, _log_response)
        return hooks

    def async_rate_limit_hooks(self) -> dict[str, list[Callable[..., Awaitable[None]]]]:
        # async 쪽(이 훅, `_async_client`, provider 의 `arun_request_*`)은 CLI 가 부르지 않지만 라이브러리로 쓸 때의 공개 API 라
        # 일부러 둔다 (provider-conventions.md '요청 메서드'). 호출부 0건은 죽은 코드가 아니다.
        hooks = self.rate_limiter.async_event_hooks()
        hooks['response'].insert(0, _alog_response)
        return hooks

    @property
    def browser_api_hosts(self) -> frozenset[str]:
        # 비면 첫 화면 호스트를 쓴다 (`BrowserOptions`). 서브클래스는 호스트 필드에서 만든다. 클래스 상수로 따로 적으면 호스트
        # 필드를 바꿨을 때 어긋나, 첫 화면이 그 호스트로 보낸 요청과 CORS 사전 요청을 요청 제한에 세지 못하고 첫 화면에서 받은
        # 그 호스트의 429 도 놓친다 (`core/browser.py` 의 `count_request`·`count_preflight`·`on_entry_rate_limited`)
        return frozenset()

    def _browser_options(self) -> BrowserOptions:
        return BrowserOptions(
            source=self.source_name, entry_url=self.browser_entry_url, ready_cookie=self.browser_ready_cookie,
            api_hosts=self.browser_api_hosts, discard_statuses=self.browser_discard_statuses,
            rate_limiter=self.rate_limiter, proxy=self.proxy, verify=self.verify, timeout=self.timeout,
            fetch_credentials=self.browser_fetch_credentials, keep_cookies=self.browser_keep_cookies,
            persistent_profile=self.browser_persistent_profile, stay_on_entry_page=self.browser_stay_on_entry_page,
            count_entry_requests=self.browser_count_entry_requests,
            block_entry_resources=self.browser_block_entry_resources, ready_timeout=self.browser_ready_timeout,
            entry_settle_seconds=self.browser_entry_settle_seconds,
            entry_block_cooldown=self.browser_entry_block_cooldown,
        )

    def browser_transport(self) -> BrowserTransport:
        """사이트 첫 화면을 연 브라우저로 요청을 보내는 transport (`core/browser.py`). 프록시도 브라우저가 쓴다."""
        return BrowserTransport(self._browser_options())

    def async_browser_transport(self) -> AsyncBrowserTransport:
        """`browser_transport()` 의 비동기판."""
        return AsyncBrowserTransport(BrowserTransport(self._browser_options()))

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
        Close the async client and the sync client too. 동기 클라이언트의 브라우저는 브라우저 스레드가 앞의 일(첫 화면이면 Chromium 띄우기에 문서
        40초·load 10초에 소스별 쿠키 기다림(기본 5초, 스카이스캐너 15초)과 스카이스캐너의 홈 기다림 3초까지)을 마친 뒤 닫아(`core/browser.py` `_BrowserThread`), 다른 스레드에서 기다려 이벤트 루프를 막지 않는다.
        """
        async_client: AsyncClient | None = self.__dict__.pop('_async_client', None)
        if async_client is not None:
            await async_client.aclose()
        await asyncio.to_thread(self.close)

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

