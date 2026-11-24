import time

import pytest
from httpx import (
    ConnectError,
    HTTPStatusError,
    ProxyError,
    ReadTimeout,
    Request,
)

from baenggipyo.core.browser import (
    REFRESH_SECONDS,
    STREAM_SECONDS_EXTENSION,
    BrowserOptions,
    CertificateError,
    CorsBlockedError,
    EntryPageError,
    _acquire_profile,
    _BlockedResponses,
    _BrowserState,
    _FailedFetch,
    _fetch_arguments,
    _proxy_settings,
    _to_response,
    returning_cookies,
    usable_cookies,
)
from baenggipyo.core.ratelimit import (
    RateLimitedError,
    RateLimiter,
    RateLimitPolicy,
    state_dir,
)
from baenggipyo.providers.naver.api.client import NaverClient

from typing import ClassVar


NOW = 1_000_000.0
ENTRY = 'https://example.test/'


def saved(opened_ago: float, cookies: list[dict], page_url: str = 'https://example.test/map') -> dict:
    return {'opened_at': NOW - opened_ago, 'page_url': page_url, 'cookies': cookies}


# --- saved cookies ----------------------------------------------------------

def test_saved_cookies_are_reused_within_refresh_window() -> None:
    cookies = [
        {'name': 'visitor', 'expires': NOW + 86400},
        {'name': 'short', 'expires': NOW - 1},
        {'name': 'session', 'expires': -1},
    ]
    # 만료된 쿠키만 빼고 넣는다. 세션 쿠키(expires -1)는 첫 화면을 다시 열 때까지 이어 쓴다
    assert [cookie['name'] for cookie in usable_cookies(saved(60, cookies), ENTRY, 'visitor', NOW) or []] == ['visitor', 'session']


def test_entry_page_reopens_after_refresh_window_or_without_ready_cookie() -> None:
    visitor_cookies = [{'name': 'visitor', 'expires': NOW + 86400}]
    assert usable_cookies(saved(REFRESH_SECONDS, visitor_cookies), ENTRY, 'visitor', NOW) is None
    # 시계가 뒤로 가 첫 화면을 연 시각이 미래면 믿지 않는다
    assert usable_cookies(saved(-60, visitor_cookies), ENTRY, 'visitor', NOW) is None
    assert usable_cookies(saved(60, [{'name': 'visitor', 'expires': NOW - 1}]), ENTRY, 'visitor', NOW) is None
    assert usable_cookies({}, ENTRY, 'visitor', NOW) is None
    # 실수로 바꿀 수 없을 만큼 큰 정수도 터지지 않고 다시 연다 (빼면 OverflowError 로 그 소스의 모든 명령이 실패했다)
    huge = {'opened_at': int('1' + '0' * 400), 'page_url': 'https://example.test/map', 'cookies': visitor_cookies}
    assert usable_cookies(huge, ENTRY, 'visitor', NOW) is None
    # 업데이트로 첫 화면 출처가 바뀌면 옛 주소로 띄우지 않는다 (띄우면 다른 출처로 보고 명령이 실패했다)
    assert usable_cookies(saved(60, visitor_cookies, 'https://other.example.test/map'), ENTRY, 'visitor', NOW) is None


def test_reopened_entry_page_keeps_persistent_cookies_only() -> None:
    cookies = [
        {'name': 'visitor', 'expires': NOW + 86400},
        {'name': 'short', 'expires': NOW - 1},
        {'name': 'session', 'expires': -1},
    ]
    # 30분 뒤 첫 화면을 다시 열 때는 브라우저를 닫았다 다시 온 사람처럼 살아 있는 영구 쿠키만 넣는다 (넣지 않으면 visitor 가
    # 새로 발급됐다). 첫 화면 출처가 바뀌었으면 넣지 않는다
    assert [cookie['name'] for cookie in returning_cookies(saved(REFRESH_SECONDS, cookies), ENTRY, NOW)] == ['visitor']
    assert returning_cookies(saved(REFRESH_SECONDS, cookies, 'https://other.example.test/map'), ENTRY, NOW) == []


# --- entry page -------------------------------------------------------------

def test_entry_429_cooldown_follows_retry_after() -> None:
    # 첫 화면 API 의 429 도 우리 요청의 429 처럼 정책 값과 Retry-After 중 긴 쪽으로 쉰다. 정책 값만 쓰면 서버가 600초
    # 쉬라고 한 사이트에 120초 뒤 다시 보냈다. 사이트 밖(광고) 응답의 Retry-After 로는 늘리지 않는다
    limiter = RateLimiter('example', RateLimitPolicy(cooldown=120))
    state = _BrowserState(BrowserOptions(
        source='example', entry_url='https://www.example.test/', api_hosts=frozenset({'api.example.test'}),
        rate_limiter=limiter,
    ))
    state.page_url = 'https://www.example.test/'
    state.on_entry_rate_limited('https://ads.example.invalid/pixel', '3600', main_document=False)
    state.on_entry_rate_limited('https://api.example.test/v2/notice', '600', main_document=False)
    with pytest.raises(RateLimitedError) as caught:
        state.finish_entry(200, None)
    assert 590 < caught.value.retry_after <= 600


def test_entry_block_cooldown_only_when_enabled() -> None:
    # 첫 화면 문서가 403 이면 켠 소스만 차단 쿨다운을 건다. 기본값은 쿨다운 없이 EntryPageError 그대로
    limiter = RateLimiter('example', RateLimitPolicy(cooldown=120))
    state = _BrowserState(BrowserOptions(
        source='example', entry_url=ENTRY, rate_limiter=limiter, entry_block_cooldown=True,
    ))
    state.page_url = ENTRY
    with pytest.raises(RateLimitedError) as caught:
        state.finish_entry(403, None)
    assert caught.value.reason == 'blocked'
    assert 100 < caught.value.retry_after <= 120
    assert state.discard
    plain = _BrowserState(BrowserOptions(source='example', entry_url=ENTRY, rate_limiter=limiter))
    plain.page_url = ENTRY
    with pytest.raises(EntryPageError) as plain_caught:
        plain.finish_entry(403, None)
    assert plain_caught.value.details['blocked'] is True


def test_entry_server_error_keeps_saved_visitor_cookies() -> None:
    state = _BrowserState(BrowserOptions(source='example', entry_url=ENTRY, ready_cookie='visitor'))
    state.page_url = ENTRY
    # 5xx 는 사이트 장애라 그 첫 화면의 쿠키로 덮지도, 다시 넣은 방문자 쿠키를 버리지도 않는다 (버리면 재시도가 새 visitor 를 받았다)
    assert not state.keeps_entry_cookies(503)
    with pytest.raises(HTTPStatusError):
        state.finish_entry(503, None)
    assert state.leave_saved and not state.discard
    # 차단 화면(4xx)은 거부된 방문자라 남기지 않고 옛 쿠키도 버린다. 429 는 쿨다운 뒤 풀렸는지 보려고 남긴다
    state.reset()
    state.page_url = ENTRY
    assert not state.keeps_entry_cookies(403)
    with pytest.raises(EntryPageError):
        state.finish_entry(403, None)
    assert state.discard
    assert state.keeps_entry_cookies(429) and state.keeps_entry_cookies(200)


def test_entry_server_error_beyond_retry_cap_stops() -> None:
    # 재시도 대기 상한(60초)보다 오래 쉬라는 5xx 는 다시 열지 않는다. 60초 뒤 다시 열면 장애 중인 사이트에 서버가 정한
    # 시간보다 일찍 첫 화면 요청을 20건쯤 다시 보냈다
    state = _BrowserState(BrowserOptions(source='example', entry_url=ENTRY, ready_cookie='visitor'))
    state.page_url = ENTRY
    with pytest.raises(EntryPageError) as caught:
        state.finish_entry(503, '300')
    assert caught.value.code == 'UPSTREAM_ERROR'
    assert caught.value.details == {'status': 503, 'retry_after_seconds': 300, 'entry_page': True}
    # docs/llm.md 가 사이트 장애를 가리키는 hint 에는 업데이트를 권하지 않게 한다
    assert caught.value.hint is not None and '사이트 장애' in caught.value.hint
    assert state.leave_saved and not state.discard
    state.reset()
    state.page_url = ENTRY
    with pytest.raises(HTTPStatusError):
        state.finish_entry(503, '60')


# --- proxy ------------------------------------------------------------------

def test_proxy_settings_translate_curl_schemes_and_reject_what_chromium_cannot_use() -> None:
    assert _proxy_settings('socks5h://127.0.0.1:1080', ENTRY) == {'server': 'socks5://127.0.0.1:1080'}
    # 에러 메시지에 비밀번호를 싣지 않는다
    for proxy in ('http://user:secret@proxy:80a', 'socks5://user:secret@127.0.0.1:1080', 'ftp://user:secret@proxy:21'):
        with pytest.raises(ProxyError) as caught:
            _proxy_settings(proxy, ENTRY)
        assert 'secret' not in str(caught.value)


def test_proxy_settings_skip_scheme_only_no_proxy_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    # 개발자 셸의 프록시 변수를 지운다. urllib 은 소문자를 먼저 봐서 https_proxy 가 있으면 아래 값을 덮었다
    for name in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)
    # 스킴만 적은 항목은 IndexError 로 모든 명령을 INTERNAL_ERROR 로 만들었다
    monkeypatch.setenv('HTTPS_PROXY', 'http://proxy:3128')
    monkeypatch.setenv('NO_PROXY', 'localhost,http://')
    assert _proxy_settings(None, ENTRY) == {'server': 'http://proxy:3128', 'bypass': 'localhost'}


# --- failed fetch -----------------------------------------------------------

FETCH_FAILED = {'requestUrl': 'https://api.example.test/v2/search', 'error': 'TypeError', 'message': 'Failed to fetch'}


def failed_fetch(error_text: str, cors_error: str | None = None, status: int | None = None, preflight: bool = False) -> _FailedFetch:
    return _FailedFetch(error_text, cors_error, (status, {}) if status is not None else None, preflight)


def test_failed_fetch_is_classified_by_chromium_event() -> None:
    request = Request('GET', FETCH_FAILED['requestUrl'])

    def convert(failed: _FailedFetch | None) -> object:
        return _to_response(request, FETCH_FAILED, 20, {}, failed)

    # 프록시에 닿지 못한 것은 재시도하지 않는 ProxyError (남긴 쿠키로 띄우면 우리 요청에서 처음 드러난다)
    with pytest.raises(ProxyError):
        convert(failed_fetch('net::ERR_PROXY_CONNECTION_FAILED'))
    with pytest.raises(ConnectError, match='ERR_NAME_NOT_RESOLVED'):
        convert(failed_fetch('net::ERR_NAME_NOT_RESOLVED'))
    # 인증서를 믿지 못한 것도 재시도하지 않는다 (ConnectError 로 내면 3번 보낸 뒤 '네트워크 상태를 확인하라'로 끝났다)
    with pytest.raises(CertificateError):
        convert(failed_fetch('net::ERR_CERT_AUTHORITY_INVALID'))
    # CORS 에 막힌 응답은 상태로 다룬다. 2xx 는 본문이 없어 CorsBlockedError
    assert convert(failed_fetch('net::ERR_FAILED', 'MissingAllowOriginHeader', 429)).status_code == 429
    with pytest.raises(CorsBlockedError):
        convert(failed_fetch('net::ERR_FAILED', 'MissingAllowOriginHeader', 200))
    # 사전 요청(OPTIONS)만 거절됐으면 429·5xx 만 응답으로 만든다. 403 을 넘기면 '없는 ID'로 읽혔다
    assert convert(failed_fetch('net::ERR_FAILED', 'PreflightInvalidStatus', 429, preflight=True)).status_code == 429
    with pytest.raises(CorsBlockedError, match='사전 요청 HTTP 403'):
        convert(failed_fetch('net::ERR_FAILED', 'PreflightMissingAllowOriginHeader', 403, preflight=True))


def test_cors_failure_waits_for_blocked_status_event() -> None:
    # CDP 는 실패 이벤트와 받은 상태 이벤트의 순서를 정하지 않는다. 실패만 보고 멈추면 막힌 429 가 CorsBlockedError 가 됐다
    url = FETCH_FAILED['requestUrl']
    blocked = _BlockedResponses()
    blocked._on_request({'requestId': '1', 'type': 'Fetch', 'request': {'url': url}})
    blocked._on_failed({'requestId': '1', 'errorText': 'net::ERR_FAILED', 'corsErrorStatus': {'corsError': 'MissingAllowOriginHeader'}})
    assert not blocked.settled(url)
    blocked._on_response({'requestId': '1', 'statusCode': 429, 'headers': {'Retry-After': '60'}})
    assert blocked.settled(url)
    assert blocked.pop(url) == _FailedFetch('net::ERR_FAILED', 'MissingAllowOriginHeader', (429, {'Retry-After': '60'}), False)


# --- stream mode ------------------------------------------------------------

def test_stream_extension_and_credentials_are_passed_to_fetch_script() -> None:
    # 스트림 모드는 요청 extension 에서 읽는다. 없으면 streamMs 가 None 이라 스크립트가 지금처럼 본문을 끝까지 읽는다
    url = 'https://api.example.test/search'
    plain = _fetch_arguments(Request('POST', url, json={}), 20, 'same-origin')[0]
    stream = _fetch_arguments(Request('POST', url, json={}, extensions={STREAM_SECONDS_EXTENSION: 30.0}), 20, 'include')[0]

    assert (plain['streamMs'], plain['credentials']) == (None, 'same-origin')
    assert (stream['streamMs'], stream['credentials']) == (30_000.0, 'include')
    # 응답 헤더·본문 조각 사이 제한은 스트림 모드와 상관없이 timeout 이다
    assert stream['timeoutMs'] == plain['timeoutMs'] == 20_000


def test_stream_body_timeout_is_read_timeout_only_for_success_status() -> None:
    # 첫 이벤트 전에 멈추면 스크립트가 상태·헤더에 BodyTimeout 을 붙여 돌려준다. 2xx 는 본문이 있어야 하는 응답이라 ReadTimeout 이다
    url = FETCH_FAILED['requestUrl']
    request = Request('POST', url)

    def stalled(status: int) -> dict:
        return {'requestUrl': url, 'url': url, 'status': status, 'headers': [], 'error': 'BodyTimeout'}

    with pytest.raises(ReadTimeout):
        _to_response(request, stalled(200), 20, {}, None)
    assert _to_response(request, stalled(429), 20, {}, None).status_code == 429


# --- rate limit record ------------------------------------------------------

def test_record_keeps_slots_for_the_policy_window() -> None:
    # 첫 화면이 보낸 요청을 남길 때 시간당 상한이 있는 정책은 1시간 안의 슬롯을 지우면 안 된다 (지우면 그 상한이 새어 나간다)
    now = time.time()
    hourly = RateLimiter('example', RateLimitPolicy(max_per_hour=30))
    minutely = RateLimiter('example-minute', RateLimitPolicy())
    for limiter in (hourly, minutely):
        with limiter._locked_state() as state:
            state['recent'] = [now - 600, now + 5]
        limiter.record(2)

    assert len(hourly._read_state()['recent']) == 4
    # 시간당 상한이 없으면 60초가 지난 슬롯은 지운다. 예약해 둔 미래 슬롯은 남고 목록은 오름차순이다
    recent = minutely._read_state()['recent']
    assert len(recent) == 3 and recent == sorted(recent)


# --- response headers -------------------------------------------------------

def test_non_ascii_response_headers_keep_server_bytes() -> None:
    # fetch 는 헤더 값을 바이트 하나당 글자 하나로 준다. 문자열 그대로 httpx 에 넣으면 UnicodeEncodeError 가 나 정상 응답이
    # PARSE_ERROR 가 됐다. Playwright 가 이미 풀어 준 값(page_headers)은 UTF-8 로 넣는다
    url = FETCH_FAILED['requestUrl']

    def convert(fetched: list[list[str]], page: dict[str, str]) -> object:
        result = {'requestUrl': url, 'url': url, 'status': 200, 'headers': fetched, 'body': ''}
        return _to_response(Request('GET', url), result, 20, {url: page}, None)

    utf8 = convert([['x-msg', '김포공항'.encode().decode('latin-1')], ['content-encoding', 'gzip']], {'x-name': '김포공항'})
    assert (utf8.headers['x-msg'], utf8.headers['x-name']) == ('김포공항', '김포공항')
    # 브라우저가 이미 푼 본문의 헤더는 뺀다
    assert 'content-encoding' not in utf8.headers
    latin1 = convert([['content-disposition', 'attachment; filename="caf\xe9.json"']], {})
    assert latin1.headers['content-disposition'] == 'attachment; filename="café.json"'


# --- browser options --------------------------------------------------------

def test_client_browser_settings_reach_browser_options() -> None:
    # 클라이언트의 클래스 변수가 모두 `BrowserOptions` 로 넘어간다 (기본값은 지금 동작 그대로)
    default = NaverClient()._browser_options()
    assert (
        default.keep_cookies, default.persistent_profile, default.stay_on_entry_page, default.count_entry_requests,
        default.block_entry_resources, default.ready_timeout, default.entry_settle_seconds,
    ) == (True, False, False, True, True, 5.0, 0.0)

    class Custom(NaverClient):
        browser_keep_cookies: ClassVar[bool] = False
        browser_persistent_profile: ClassVar[bool] = True
        browser_stay_on_entry_page: ClassVar[bool] = True
        browser_count_entry_requests: ClassVar[bool] = False
        browser_block_entry_resources: ClassVar[bool] = False
        browser_ready_timeout: ClassVar[float] = 15.0
        browser_entry_settle_seconds: ClassVar[float] = 3.0

    options = Custom()._browser_options()
    assert (
        options.keep_cookies, options.persistent_profile, options.stay_on_entry_page, options.count_entry_requests,
        options.block_entry_resources, options.ready_timeout, options.entry_settle_seconds,
    ) == (False, True, True, False, False, 15.0, 3.0)


# --- profile directory ------------------------------------------------------

def test_profile_dir_falls_back_to_temporary_when_another_holds_the_lock() -> None:
    first = _acquire_profile('example')
    try:
        assert not first.temporary and first.path == state_dir() / 'browser' / 'example-profile'
        # 다른 쪽이 잠금을 쥔 동안에는 새 임시 폴더를 받고, 닫을 때 그 폴더를 지운다
        second = _acquire_profile('example')
        assert second.temporary and second.path != first.path and second.path.is_dir()
        second.release(remove=False)
        assert not second.path.exists()
    finally:
        first.path.mkdir(parents=True, exist_ok=True)
        first.release(remove=False)
    # 잠금 폴더는 막혔을 때만 지운다. 풀린 뒤에는 다시 얻는다
    assert first.path.is_dir()
    third = _acquire_profile('example')
    assert not third.temporary
    third.release(remove=True)
    assert not third.path.exists()
