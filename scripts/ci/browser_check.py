"""이 운영체제에서 baenggipyo 브라우저가 제대로 도는지 가짜 사이트로 확인한다 (CI 의 '브라우저 확인' 잡, `.github/workflows/ci.yml`).

pytest 는 가짜 응답만 써서 Chromium 을 띄우지 않는다. 그래서 운영체제마다 달라지는 브라우저 쪽(Chromium 띄우기, 브라우저 안의
요청, 막힌 응답 알아보기, Ctrl+C, 끝난 뒤 프로세스 정리)은 여기서 본다. 실제 사이트에는 요청하지 않는다. 이 컴퓨터에 웹 서버
두 개를 띄운다: 첫 화면과 데이터를 주는 사이트(entry)와, 다른 출처에서 CORS 없이 429 를 주는 사이트(blocked). 상태 폴더는
임시 폴더라 실제 쿠키·요청 제한 기록을 건드리지 않는다. CI 는 `pip install .` 로 설치한 baenggipyo 의 Python 으로 돌린다.

    python scripts/ci/browser_check.py

1. 첫 화면을 열고, 그 안에서 보낸 요청(GET·POST)이 데이터와 쿠키를 받아 오는지. 1MB 넘는 본문을 브라우저 안에 두었다가 나눠
   받는지(`BrowserTransport._handle_request`), 응답 헤더가 늦거나 본문이 멈추면 timeout 에 맞춰 `ReadTimeout` 으로 끝나는지, 본문이 멈춘 429 는
   429 로 알아보는지도 본다. 가짜 사이트라야 만들 수 있는 큰 본문·멈춘 응답 경로라 여기서만 돈다
2. 다시 띄우면 첫 화면을 열지 않고 남긴 쿠키로 바로 요청하는지
3. 다른 출처가 CORS 없이 준 429 를 Retry-After 와 함께 429 로 알아보는지. 브라우저는 이런 응답을 가려 fetch 가 이유 없이
   실패하므로 baenggipyo 는 CDP 네트워크 이벤트로 상태를 알아낸다 (`_BlockedResponses`). 그래야 요청 제한이 쿨다운을 건다.
   이 확인에서만 Chromium 의 로컬 네트워크 접근 차단을 끈다 (`allow_local_network`)
4. 느린 첫 화면을 여는 중에 Ctrl+C 를 받으면 1초 안에 끝나고, 끝난 뒤 Chromium·드라이버(node)가 남지 않는지.
   CI 에는 키보드가 없어 `_thread.interrupt_main()` 으로 흉내 낸다. 메인 스레드가 브라우저 스레드를 기다리는 동안 Ctrl+C 를
   듣는지가 Windows 에서 갈리는 곳이라(`WAIT_POLL_SECONDS`) 그 흉내로 확인된다
5. 비동기 transport(`AsyncBrowserTransport`)도 첫 화면을 열고 GET·POST 를 보내는지. CLI·매일 감시는 동기 transport 만 써서
   비동기 쪽은 여기서만 띄워 본다. 비동기 transport 는 동기 transport 를 감싸 브라우저 스레드에 일을 넘기는데, 그 넘기기
   (본문 미리 읽기·`await` 로 기다리기·닫기)가 맞는지 여기서 알 수 있다
6. 사이트 API 에 닿은 요청을 요청 제한에 빠짐없이 한 번씩 세는지. 첫 화면이 스스로 부른 API 와 브라우저가 다른 출처로 보낸
   CORS 사전 요청(OPTIONS)은 httpx 훅을 거치지 않아 transport 가 따로 센다 (`RateLimiter.record`, `count_preflight`).
   AGENTS.md 의 '모든 HTTP 요청은 요청 제한을 거친다'를 브라우저에서 지키는 계산이라, Chromium·patchright 가 이벤트를 바꿔
   빠지거나 두 번 세면 여기서 알 수 있다. 이 확인은 첫 화면 사이트와 CORS 를 허용하는 다른 출처 API 사이트를 따로 띄운다
7. 스트림 모드(`STREAM_SECONDS_EXTENSION`)가 `text/event-stream` 을 브라우저 안에서 줄 단위로 읽어 마지막 `data:` 줄만 돌려주는지.
   이벤트 3개 뒤 정상 끝(a), 이벤트가 계속 와서 `s` 초를 넘김(b), 첫 이벤트 전 멈춤(c), 이벤트 하나 뒤 멈춤(d), 이벤트 하나 뒤 연결이
   끊김(e), 1MB 넘는 이벤트(f)를 본다. 이벤트마다 그때까지의 결과 전체를 다시 보내는 스트림이 쓰는 경로다
8. `fetch_credentials='include'` 일 때만 다른 출처 API 에 쿠키가 실리는지. 가짜 API 는 `Access-Control-Allow-Credentials: true` 와
   첫 화면의 정확한 출처를 답한다 (credentials 요청은 `*` 를 거절한다)
9. `stay_on_entry_page=True` 면 요청을 빈 페이지가 아니라 열어 둔 첫 화면 탭에서 보내는지. 가짜 첫 화면이 계속 보내는 요청(tick)이
   우리 요청이 끝난 뒤에도 이어지면 그 페이지가 살아 있는 것이다 (기본값은 빈 페이지로 바꿔 멈춘다)
10. `persistent_profile=True` 면 브라우저 기록 폴더를 이어 쓰는지. 첫 transport 가 첫 화면 JS 로 `localStorage`·오래가는 쿠키에 남긴 값을
   닫은 뒤 두 번째 transport 가 받는다. 첫 transport 가 열려 있는 동안 띄운 transport 는 이어 받지 못하고 임시 폴더를 쓰며,
   닫으면 그 임시 폴더가 지워진다
11. `persistent_profile=True` 에서 우리 요청이 `discard_statuses`(403)를 받으면 닫은 뒤 기록 폴더가 지워지는지
12. `count_entry_requests=False` 면 첫 화면이 스스로 보낸 API 요청이 요청 제한 기록에 들지 않고, 그 하위 요청이 받은 429 로 쿨다운이
   걸리거나 요청이 멈추지 않는지 (기본값은 세고 쿨다운을 건다)
13. `block_entry_resources=False` 면 첫 화면의 이미지·스타일시트 요청이 서버에 닿는지 (기본값은 닿지 않는다)
14. 기록 폴더를 이어 쓸 때 폴더에 이미 `ready_cookie` 가 있어도 첫 화면이 쿠키를 새로 심을 때까지 기다린 뒤 요청하는지
15. `entry_settle_seconds` 만큼 새로 연 첫 화면에서 첫 요청이 늦게 나가는지
16. 첫 화면 문서가 본문 없는(`Content-Length: 0`) 403·429·503 이어도 본문이 있을 때처럼 판정하는지 (403 은 차단 쿨다운 `RateLimitedError(reason='blocked')`,
   429 는 `Retry-After` 를 반영한 쿨다운, 503 은 첫 화면 HTTP 503 에러). Chromium 은 이런 문서에서 goto 를 `net::ERR_HTTP_RESPONSE_CODE_FAILURE` 로
   실패시켜 응답을 돌려주지 않는다. 그리고 첫 화면이 다른 출처의 토큰 실린 주소로 넘어가면 에러 message 에 출처만 싣고 경로·쿼리는 싣지 않는지

하나라도 틀리면 exit 1 이다.
"""
import _thread
import asyncio
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import (
    contextmanager,
    suppress,
)
from http.server import (
    BaseHTTPRequestHandler,
    ThreadingHTTPServer,
)
from pathlib import Path
from urllib.parse import urlsplit

from typing import (
    Any,
    Literal,
)


# 실제 쿠키·요청 제한 기록 대신 임시 폴더를 쓴다 (baenggipyo 는 부를 때마다 이 환경 변수를 읽는다)
# 환경 변수가 없을 때만 만들고 끝날 때 지운다. `--interrupt-child` 자식은 부모의 환경 변수를 물려받아 만들지 않으므로 정리는 만든 프로세스만 한다
if 'BAENGGIPYO_STATE_DIR' not in os.environ:
    _state_dir = tempfile.mkdtemp(prefix='baenggipyo-browser-check-')
    os.environ['BAENGGIPYO_STATE_DIR'] = _state_dir
    atexit.register(shutil.rmtree, _state_dir, ignore_errors=True)

from httpx import (  # noqa: E402
    AsyncClient,
    Client,
    HTTPStatusError,
    ReadTimeout,
    Response,
    TransportError,
)
from scrapling.engines._browsers import _base as scrapling_base  # noqa: E402

from baenggipyo.core.browser import (  # noqa: E402
    BODY_CHUNK_BYTES,
    STREAM_SECONDS_EXTENSION,
    AsyncBrowserTransport,
    BrowserOptions,
    BrowserTransport,
)
from baenggipyo.core.errors import BaenggipyoError  # noqa: E402
from baenggipyo.core.ratelimit import (  # noqa: E402
    RateLimitedError,
    RateLimiter,
    RateLimitPolicy,
    state_dir,
)


# 첫 화면 JS 가 심는 쿠키. baenggipyo 는 이 쿠키가 생길 때까지 기다리고, 남긴 쿠키를 쓸 수 있는지 이것으로 본다
READY_COOKIE: str = 'ready'
# 다른 출처 요청에 붙이는 헤더. 기본 허용 헤더가 아니라 브라우저가 CORS 사전 요청(OPTIONS)을 먼저 보낸다 (6번 확인)
PREFLIGHT_HEADER: str = 'x-check'
# 1MB 넘는 응답 본문. baenggipyo 는 이보다 큰 본문을 evaluate 한 번에 받지 않고 브라우저 안에 두었다가 나눠 읽는다 (`BODY_CHUNK_BYTES`)
BIG_BODY: bytes = json.dumps({'data': 'x' * (2 * BODY_CHUNK_BYTES + 1)}).encode()
# 멈춘 본문 응답이 첫 조각 뒤 침묵하는 시간과 그 요청의 timeout. timeout 이 더 짧아야 본문 유휴 timeout 이 난다
STALL_SECONDS: float = 5
STALL_TIMEOUT_SECONDS: float = 2
# 본문이 멈춘 429 응답의 Retry-After. 본문을 다 받지 못해도 상태·헤더로 429 를 알아봐야 요청 제한이 쿨다운을 건다
STALL_RETRY_AFTER: str = '7'
# 스트림 확인(7번)의 이벤트 사이 간격과 스트림 모드 시간(`STREAM_SECONDS_EXTENSION`)·요청 timeout. 끝없이 오는 스트림은 그 간격으로
# 이벤트를 보내고, 멈추는 스트림은 `STALL_SECONDS` 동안 침묵한다 (timeout 이 더 짧아야 본문 유휴 timeout 이 난다)
EVENT_GAP_SECONDS: float = 0.2
STREAM_SECONDS: float = 2
STREAM_TIMEOUT_SECONDS: float = 2
# 스트림 확인에서 요청 하나가 끝나야 하는 한계. 끝없이 오는 스트림이 `STREAM_SECONDS` 근처에서 끝나는지 잰다
STREAM_OVERRUN_SECONDS: float = 1.5
# 스트림 확인(f)의 1MB 넘는 이벤트 글자. 주기가 36 이라 조각(`BODY_CHUNK_BYTES`)이 어긋나거나 뒤섞이면 글자가 달라진다
BIG_EVENT_TEXT: bytes = (b'0123456789abcdefghijklmnopqrstuvwxyz' * (2 * BODY_CHUNK_BYTES // 36 + 1))[:2 * BODY_CHUNK_BYTES + 1]
# 9~15번 확인. 우리 요청이 끝난 뒤 가짜 첫 화면의 tick 요청(100ms 간격)이 이어지는지 보는 시간
TICK_WAIT_SECONDS: float = 0.8
# `entry_settle_seconds` 확인(15번)에서 쓰는 값. 가짜 첫 화면은 이 확인 없이도 0.3초 뒤 준비 쿠키를 심고 `READY_COOKIE_SETTLE_SECONDS` 가 더 붙는다
ENTRY_SETTLE_SECONDS: float = 2.0
# 기록 폴더 확인(14번)에서 가짜 첫 화면이 준비 쿠키를 (새로) 심기까지 기다리는 시간. `READY_COOKIE_TIMEOUT_SECONDS`(5초)보다 짧아야 한다
PLANT_DELAY_MS: int = 1500
# 느린 첫 화면이 응답하기까지 걸리는 시간. Ctrl+C 가 이보다 훨씬 빨리 끝내야 한다
SLOW_SECONDS: float = 30
# Ctrl+C 를 보낸 뒤 KeyboardInterrupt 가 나고 클라이언트를 닫기까지 허용하는 시간
INTERRUPT_LIMIT_SECONDS: float = 1.0
# 끝난 뒤 Chromium·드라이버가 사라지기를 기다리는 시간
LEFTOVER_WAIT_SECONDS: float = 10
# 이 문자열이 명령줄에 든 프로세스를 baenggipyo 가 띄운 Chromium·드라이버로 본다 (Chromium 은 ms-playwright 폴더에 있고,
# 드라이버는 `node ... run-driver` 다. 운영체제마다 같다)
BROWSER_PROCESS_MARKERS: tuple[str, ...] = ('ms-playwright', 'run-driver')
# 가짜 첫 화면이 주는 1x1 PNG (9~15번 확인의 이미지)
TINY_PNG: bytes = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082'
)

failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(f"{'OK  ' if ok else 'FAIL'} {what}", flush=True)
    if not ok:
        failures.append(what)


class QuietHandler(BaseHTTPRequestHandler):
    """요청마다 찍는 로그를 끈 핸들러. `site` 는 `Site` 가 붙인다."""

    site: 'Site'

    def log_message(self, format: str, *args: object) -> None:
        pass


class Site:
    """가짜 사이트 하나 (127.0.0.1 의 빈 포트). 받은 요청 수를 경로마다 센다."""

    def __init__(self, handler: type[QuietHandler]) -> None:
        self.counts: dict[str, int] = {}
        # 느린 첫 화면 요청이 들어왔는지 (Ctrl+C 를 보낼 때를 고른다)
        self.slow_started = threading.Event()
        # 이름마다 서버가 본 시각(`time.monotonic`). 9~15번 확인이 요청 순서를 비교한다
        self.times: dict[str, float] = {}
        bound = type(handler.__name__, (handler,), {'site': self})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), bound)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}/'


class EntryHandler(QuietHandler):

    def _send(self, status: int, body: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _count(self) -> None:
        path = self.path.split('?')[0]
        self.site.counts[path] = self.site.counts.get(path, 0) + 1

    def do_GET(self) -> None:
        self._count()
        if self.path == '/':
            # 서버 쿠키(sid)와 첫 화면 JS 쿠키(ready)를 같이 심는다
            page = f"<html><body><script>document.cookie = '{READY_COOKIE}=1; path=/'</script></body></html>"
            self._send(200, page.encode(), 'text/html', {'Set-Cookie': 'sid=abc; Path=/'})
        elif self.path == '/api/data':
            payload = {'ok': True, 'cookie': self.headers.get('Cookie', '')}
            self._send(200, json.dumps(payload).encode(), 'application/json')
        elif self.path == '/api/big':
            self._send(200, BIG_BODY, 'application/json')
        elif self.path == '/api/stall-head':
            # 응답 헤더를 보내지 않고 멈춘다
            time.sleep(STALL_SECONDS)
        elif self.path in ('/api/stall', '/api/stall-429'):
            # 본문 한 조각을 보내고 멈춘다 (Content-Length 없이 보내 끝을 알 수 없게 한다)
            self.send_response(429 if self.path == '/api/stall-429' else 200)
            self.send_header('Content-Type', 'application/json')
            if self.path == '/api/stall-429':
                self.send_header('Retry-After', STALL_RETRY_AFTER)
            self.end_headers()
            self.wfile.write(b'["a"')
            self.wfile.flush()
            time.sleep(STALL_SECONDS)
        elif self.path == '/slow':
            self.site.slow_started.set()
            time.sleep(SLOW_SECONDS)
            self._send(200, b'<html></html>', 'text/html')
        else:
            self._send(404, b'', 'text/plain')

    def do_POST(self) -> None:
        self._count()
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self._send(200, body, 'application/json')


class FirstPageHandler(EntryHandler):
    """첫 화면이 하위 요청·이미지·스타일시트를 부르고 tick 요청을 계속 보내는 사이트 (9·12·13·15번 확인).

    첫 화면 JS 는 같은 출처 API 를 3번 부르고(`limited` 면 429 를 주는 API 도), 0.3초 뒤 준비 쿠키를 심고, 100ms 마다 tick 을 보낸다.
    서버는 첫 화면 문서를 받은 시각(`entry`)과 우리 요청(`/api/data`)을 처음 받은 시각(`data`)을 `site.times` 에 남긴다.
    """

    limited: bool = False

    def do_GET(self) -> None:
        path = self.path.split('?')[0]
        if path == '/':
            self._count()
            self.site.times.setdefault('entry', time.monotonic())
            extra = ", fetch('/api/limited')" if self.limited else ''
            page = (
                '<html><head><link rel="stylesheet" href="/static/style.css"></head><body><img src="/static/pic.png"><script>'
                f"Promise.all([fetch('/api/sub-a'), fetch('/api/sub-b'), fetch('/api/sub-c'){extra}])"
                f".then(() => setTimeout(() => {{ document.cookie = '{READY_COOKIE}=1; path=/' }}, 300));"
                "setInterval(() => fetch('/api/tick'), 100)"
                '</script></body></html>'
            )
            self._send(200, page.encode(), 'text/html')
        elif path in ('/api/sub-a', '/api/sub-b', '/api/sub-c', '/api/tick'):
            self._count()
            self._send(200, b'{}', 'application/json')
        elif path == '/api/limited':
            self._count()
            self._send(429, b'{}', 'application/json', {'Retry-After': '7'})
        elif path == '/static/style.css':
            self._count()
            self._send(200, b'body { color: red }', 'text/css')
        elif path == '/static/pic.png':
            self._count()
            self._send(200, TINY_PNG, 'image/png')
        else:
            if path == '/api/data':
                self.site.times.setdefault('data', time.monotonic())
            super().do_GET()


class ProfileEntryHandler(EntryHandler):
    """기록 폴더에 남는 값을 쓰는 첫 화면 (10·11·14번 확인).

    첫 화면 JS 는 `localStorage` 의 이전 값과 `ready` 쿠키가 이미 있었는지를 서버에 알리고(`seen <이전 값> <yes|no>`), 값을 남긴
    뒤 `PLANT_DELAY_MS` 가 지나면 오래가는 준비 쿠키를 (매번 다른 값으로) 심고 서버에 알린다 (`planted`). 서버는 `planted` 와 우리
    요청(`/api/data`)이 마지막으로 온 시각을 `site.times` 에 남긴다.
    """

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        if parts.path == '/':
            self._count()
            script = (
                "const prev = localStorage.getItem('marker') || 'none';"
                f"const had = document.cookie.includes('{READY_COOKIE}=') ? 'yes' : 'no';"
                "localStorage.setItem('marker', 'v1');"
                "fetch('/api/seen?prev=' + prev + '&cookie=' + had).then(() => setTimeout(() => {"
                f" document.cookie = '{READY_COOKIE}=' + Date.now() + '; path=/; max-age=3600'; fetch('/api/planted') }}, {PLANT_DELAY_MS}))"
            )
            self._send(200, f'<html><body><script>{script}</script></body></html>'.encode(), 'text/html')
        elif parts.path == '/api/seen':
            query = dict(item.split('=', 1) for item in parts.query.split('&'))
            key = f"seen {query['prev']} {query['cookie']}"
            self.site.counts[key] = self.site.counts.get(key, 0) + 1
            self._send(200, b'{}', 'application/json')
        elif parts.path == '/api/planted':
            self.site.times['planted'] = time.monotonic()
            self._send(200, b'{}', 'application/json')
        elif parts.path == '/api/forbidden':
            self._send(403, b'{}', 'application/json')
        else:
            if parts.path == '/api/data':
                self.site.times['data'] = time.monotonic()
            super().do_GET()


class EmptyEntryHandler(EntryHandler):
    """첫 화면 문서를 본문 없이 4xx·5xx 로 주거나 다른 출처의 토큰 실린 주소로 넘기는 사이트 (16번 확인). `mode` 로 고른다."""

    mode: str = ''
    other_url: str = ''

    def do_GET(self) -> None:
        self._count()
        if self.path != '/':
            self._send(404, b'', 'text/plain')
        elif self.mode == 'empty-403':
            self._send(403, b'', 'text/html')
        elif self.mode == 'empty-429':
            self._send(429, b'', 'text/html', {'Retry-After': '600'})
        elif self.mode == 'empty-503':
            self._send(503, b'', 'text/html')
        elif self.mode == 'redirect':
            self._send(302, b'', 'text/html', {'Location': f'{self.other_url}login?token=secret-token&next=1'})


class LoginHandler(EntryHandler):
    """다른 출처에서 `/login` 만 본문 있는 200 으로 주는 사이트 (16번 확인의 넘어간 곳)."""

    def do_GET(self) -> None:
        self._count()
        self._send(200, b'<html><body>login</body></html>', 'text/html')


class BlockedHandler(QuietHandler):
    """다른 출처. `Access-Control-Allow-Origin` 없이 429 를 줘, 브라우저 fetch 는 상태를 읽지 못한다 (사이트 앞 방화벽 차단처럼)."""

    def do_GET(self) -> None:
        self.send_response(429)
        self.send_header('Retry-After', '7')
        self.send_header('Content-Length', '0')
        self.end_headers()


class CountingEntryHandler(EntryHandler):
    """첫 화면이 같은 출처 API 를 3번, 다른 출처 API(`api_url`)를 사전 요청이 따라가는 헤더로 1번 부른다 (6번 확인).

    요청이 모두 끝난 뒤에 준비 쿠키를 심는다. baenggipyo 는 이 쿠키를 보고 첫 화면을 떠나므로, 떠날 때 서버가 받은 수가 정해진다.
    """

    api_url: str = ''

    def do_GET(self) -> None:
        self._count()
        if self.path != '/':
            self._send(200, b'{}', 'application/json')
            return
        script = (
            "Promise.all([fetch('/api/a'), fetch('/api/b'), fetch('/api/c'),"
            f" fetch('{self.api_url}api/entry', {{headers: {{'{PREFLIGHT_HEADER}': '1'}}}})])"
            f".then(() => {{ document.cookie = '{READY_COOKIE}=1; path=/' }})"
        )
        self._send(200, f'<html><body><script>{script}</script></body></html>'.encode(), 'text/html')


class CorsApiHandler(QuietHandler):
    """다른 출처 API. CORS 와 `PREFLIGHT_HEADER` 를 허용하고, 받은 요청을 메서드와 경로로 센다 (6번 확인)."""

    def _reply(self, status: int, body: bytes) -> None:
        key = f"{self.command} {self.path.split('?')[0]}"
        self.site.counts[key] = self.site.counts.get(key, 0) + 1
        self.send_response(status)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', PREFLIGHT_HEADER)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self._reply(204, b'')

    def do_GET(self) -> None:
        self._reply(200, b'{"ok": true}')


class StreamEntryHandler(EntryHandler):
    """첫 화면과 `text/event-stream` 경로(`/api/sse/<경우>`)를 주는 사이트 (7번 확인).

    스트림은 Transfer-Encoding: chunked 로 보낸다. 끝을 알리는 마지막 조각 없이 연결을 끊으면 브라우저가 전송 오류로 읽어, 정상
    끝(마지막 조각)과 끊김을 가를 수 있다. 서버가 마지막으로 보낸 이벤트 번호는 `site.counts['last-event']` 에 남는다.
    """

    protocol_version = 'HTTP/1.1'

    def _start_stream(self) -> None:
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Transfer-Encoding', 'chunked')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True

    def _chunk(self, data: bytes) -> None:
        self.wfile.write(f'{len(data):x}\r\n'.encode() + data + b'\r\n')
        self.wfile.flush()

    def _event(self, number: int) -> None:
        self.site.counts['last-event'] = number
        self._chunk(f'data: {{"n": {number}}}\n\n'.encode())

    def do_GET(self) -> None:
        if not self.path.startswith('/api/sse/'):
            super().do_GET()
            return
        self._count()
        case = self.path.rsplit('/', 1)[-1]
        self._start_stream()
        try:
            if case == 'normal':
                for number in (1, 2, 3):
                    self._event(number)
                    time.sleep(EVENT_GAP_SECONDS)
                self.wfile.write(b'0\r\n\r\n')
            elif case == 'endless':
                # 클라이언트가 읽기를 그만두면 쓰기가 실패해 끝난다. 그래도 안 끝나는 경우를 위해 상한을 둔다
                for number in range(1, int(60 / EVENT_GAP_SECONDS)):
                    self._event(number)
                    time.sleep(EVENT_GAP_SECONDS)
            elif case == 'silent':
                time.sleep(STALL_SECONDS)
            elif case == 'one-stall':
                self._event(1)
                time.sleep(STALL_SECONDS)
            elif case == 'one-drop':
                # 마지막 조각 없이 연결을 닫는다 (이 함수가 끝나면 `close_connection` 으로 소켓이 닫힌다)
                self._event(1)
            elif case == 'big':
                self._chunk(b'data: ' + BIG_EVENT_TEXT + b'\n\n')
                self.wfile.write(b'0\r\n\r\n')
        except OSError:
            # 클라이언트가 먼저 연결을 닫았다
            pass


class CredentialsApiHandler(QuietHandler):
    """다른 출처 API. credentials 요청을 받는 CORS 응답을 준다 (8번 확인).

    `Access-Control-Allow-Credentials: true` 와 첫 화면의 정확한 출처(`allowed_origin`)를 답한다. `*` 는 credentials 요청을 거절한다.
    받은 Cookie 헤더를 본문으로 돌려준다.
    """

    allowed_origin: str = ''

    def _cors(self) -> None:
        self.send_header('Access-Control-Allow-Origin', self.allowed_origin)
        self.send_header('Access-Control-Allow-Credentials', 'true')
        self.send_header('Access-Control-Allow-Headers', PREFLIGHT_HEADER)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self) -> None:
        body = json.dumps({'cookie': self.headers.get('Cookie', '')}).encode()
        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def client(source: str, entry_url: str) -> Client:
    options = BrowserOptions(source=source, entry_url=entry_url, ready_cookie=READY_COOKIE)
    return Client(transport=BrowserTransport(options), timeout=20)


def check_requests(entry: Site) -> None:
    with client('browser-check', entry.url) as first:
        data = first.get(f'{entry.url}api/data')
        check(data.status_code == 200 and data.json().get('ok') is True, '1. 첫 화면을 열고 GET 요청으로 데이터를 받음')
        cookie = data.json().get('cookie', '')
        check(f'{READY_COOKIE}=1' in cookie and 'sid=abc' in cookie, f'1. 요청에 첫 화면이 심은 쿠키가 실림 ({cookie!r})')
        echo = first.post(f'{entry.url}api/echo', json={'q': '김포공항'})
        check(echo.json() == {'q': '김포공항'}, '1. POST 본문이 그대로 감')
        big = first.get(f'{entry.url}api/big')
        check(
            big.status_code == 200 and big.content == BIG_BODY,
            f'1. 1MB 넘는 본문({len(BIG_BODY)}바이트)을 브라우저 안에 두었다가 나눠 받음 (HTTP {big.status_code}, {len(big.content)}바이트)',
        )
        # 너무 일찍 끝나도(타이머 단위 실수 등으로 정상 응답이 잘림) 실패로 본다
        for what, path, said in (('헤더가 오지 않는 응답이', 'stall-head', '응답이'), ('멈춘 본문이', 'stall', '응답 본문이')):
            started = time.monotonic()
            try:
                first.get(f'{entry.url}api/{path}', timeout=STALL_TIMEOUT_SECONDS)
            except ReadTimeout as error:
                took = time.monotonic() - started
                check(
                    said in str(error) and STALL_TIMEOUT_SECONDS * 0.9 <= took < STALL_SECONDS,
                    f'1. {what} {STALL_TIMEOUT_SECONDS:g}초 timeout 에 ReadTimeout 으로 끝남 ({took:.1f}초, {error})',
                )
            else:
                check(False, f'1. {what} ReadTimeout 으로 끝남 (끝나지 않고 응답이 왔다)')
        limited = first.get(f'{entry.url}api/stall-429', timeout=STALL_TIMEOUT_SECONDS)
        retry_after = limited.headers.get('retry-after')
        check(
            limited.status_code == 429 and retry_after == STALL_RETRY_AFTER,
            f'1. 본문이 멈춘 429 를 Retry-After 와 함께 429 로 알아봄 (HTTP {limited.status_code}, Retry-After {retry_after})',
        )
    check(entry.counts.get('/') == 1, f"1. 첫 화면을 한 번 열었음 ({entry.counts.get('/')}번)")

    with client('browser-check', entry.url) as second:
        data = second.get(f'{entry.url}api/data')
        check(entry.counts.get('/') == 1, f"2. 다시 띄울 때 첫 화면을 열지 않음 (첫 화면 {entry.counts.get('/')}번)")
        check(f'{READY_COOKIE}=1' in data.json().get('cookie', ''), '2. 남긴 쿠키로 요청함')


def check_async_requests(entry: Site) -> None:
    async def run() -> tuple[Response, Response]:
        options = BrowserOptions(source='browser-check-async', entry_url=entry.url, ready_cookie=READY_COOKIE)
        transport = AsyncBrowserTransport(BrowserTransport(options))
        async with AsyncClient(transport=transport, timeout=20) as checker:
            return (
                await checker.get(f'{entry.url}api/data'),
                await checker.post(f'{entry.url}api/echo', json={'q': '김포공항'}),
            )

    data, echo = asyncio.run(run())
    cookie = data.json().get('cookie', '') if data.status_code == 200 else ''
    check(f'{READY_COOKIE}=1' in cookie, f'5. 비동기 transport 로 첫 화면을 열고 쿠키를 실어 GET 함 ({cookie!r})')
    check(echo.json() == {'q': '김포공항'}, '5. 비동기 transport 로 POST 본문이 그대로 감')


@contextmanager
def allow_local_network() -> Iterator[None]:
    """(이 확인에서만) Chromium 의 로컬 네트워크 접근 차단(Local Network Access)을 끈 채 브라우저를 띄우게 한다.

    baenggipyo 는 첫 화면 탭을 브라우저 안에서 만든 빈 페이지로 바꿔 두는데(`_fulfill_blank`), Chromium 은 주소가 없는 이 페이지를
    인터넷 페이지로 보고 127.0.0.1 의 다른 포트(가짜 차단 사이트)로 가는 요청을 `LocalNetworkAccessPermissionDenied` 로
    막는다 (2026-11-10 로컬 실측). 실제 사이트는 인터넷 주소라 이 차단과 무관하다.

    scrapling 이 넘기는 `--disable-features` 값에 하나를 더한다. 같은 스위치를 하나 더 넘기면 안 된다: scrapling 은
    extra_flags 를 받으면 플래그를 `list(set(...))` 로 합쳐 순서가 실행마다(PYTHONHASHSEED) 바뀌고, Chromium 은 같은 스위치의
    마지막 값만 써서 이 확인이 무작위로 실패했다 (2026-11-10 실측: 시드 0·1·5 실패, 2·3·4 통과). scrapling 은 이 목록을
    import 할 때 자기 모듈로 가져가 그쪽(`_base`)을 바꾼다. 목록을 옮기거나 `--disable-features` 를 빼면 여기서 실패해 알 수 있다.
    """
    original = (scrapling_base.DEFAULT_ARGS, scrapling_base.STEALTH_ARGS)
    patched = tuple(
        tuple(f'{arg},LocalNetworkAccessChecks' if arg.startswith('--disable-features=') else arg for arg in args)
        for args in original
    )
    if patched == original:
        raise RuntimeError('scrapling 이 --disable-features 를 넘기지 않아 로컬 네트워크 접근 차단을 끌 수 없다')
    scrapling_base.DEFAULT_ARGS, scrapling_base.STEALTH_ARGS = patched  # type: ignore[assignment]
    try:
        yield
    finally:
        scrapling_base.DEFAULT_ARGS, scrapling_base.STEALTH_ARGS = original


def check_blocked(entry: Site, blocked: Site) -> None:
    with allow_local_network(), client('browser-check', entry.url) as checker:
        limited = checker.get(f'{blocked.url}limited')
    check(
        limited.status_code == 429 and limited.headers.get('retry-after') == '7',
        f"3. CORS 없이 막힌 429 를 알아봄 (HTTP {limited.status_code}, Retry-After {limited.headers.get('retry-after')})",
    )


def check_request_counts() -> None:
    api = Site(CorsApiHandler)
    entry = Site(type('CountingEntry', (CountingEntryHandler,), {'api_url': api.url}))
    hosts = frozenset({urlsplit(entry.url).netloc, urlsplit(api.url).netloc})
    limiter = RateLimiter('browser-check-count', RateLimitPolicy(min_interval=0, max_per_minute=1000))
    transport = BrowserTransport(BrowserOptions(
        source='browser-check-count', entry_url=entry.url, ready_cookie=READY_COOKIE, api_hosts=hosts,
        rate_limiter=limiter,
    ))
    # 실제 클라이언트처럼 우리 요청은 httpx 훅이 센다 (`BaseHTTPClient.rate_limit_hooks`)
    with allow_local_network(), Client(transport=transport, timeout=20, event_hooks=limiter.event_hooks()) as counted:
        ours = counted.get(f'{api.url}api/ours', headers={PREFLIGHT_HEADER: '1'})
    check(
        ours.status_code == 200 and api.counts.get('OPTIONS /api/ours') == 1,
        f"6. 다른 출처로 보낸 우리 요청에 사전 요청이 따라감 (HTTP {ours.status_code}, OPTIONS {api.counts.get('OPTIONS /api/ours')}번)",
    )
    # 첫 화면 문서(/)와 favicon 은 사이트 API 가 아니라 세지 않는다
    reached = sum(count for path, count in entry.counts.items() if path.startswith('/api/')) + sum(api.counts.values())
    recorded = limiter.status()['requests_last_minute']
    check(reached == recorded, f'6. 사이트 API 에 닿은 요청을 요청 제한에 한 번씩 셈 (서버가 받은 {reached}건, 기록 {recorded}건)')
    if reached != recorded:
        print(f'     첫 화면 사이트 {entry.counts}, 다른 출처 API {api.counts}', flush=True)


def stream_get(
    checker: Client, url: str, seconds: float = STREAM_SECONDS, timeout: float = STREAM_TIMEOUT_SECONDS,
) -> tuple[Response | None, Exception | None, float]:
    """스트림 모드로 GET 한다. (응답, 예외, 걸린 초). 둘 중 하나만 있다."""
    started = time.monotonic()
    try:
        with checker.stream('GET', url, timeout=timeout, extensions={STREAM_SECONDS_EXTENSION: seconds}) as response:
            response.read()
    except TransportError as error:
        return None, error, time.monotonic() - started
    return response, None, time.monotonic() - started


def check_streams() -> None:
    site = Site(StreamEntryHandler)
    base = f'{site.url}api/sse/'
    with client('browser-check-stream', site.url) as streaming:
        # 첫 화면을 열고 브라우저를 띄우는 시간이 아래 걸린 시간에 들어가지 않게 먼저 한 번 부른다
        streaming.get(f'{site.url}api/data')

        response, error, _ = stream_get(streaming, f'{base}normal')
        check(
            response is not None and response.status_code == 200 and response.content == b'data: {"n": 3}\n',
            f'7a. 이벤트 3개 뒤 정상 끝나면 마지막 data 줄 하나만 받음 ({response.content if response else error!r})',
        )

        response, error, took = stream_get(streaming, f'{base}endless')
        last = site.counts.get('last-event', 0)
        number = -1
        if response is not None and response.content.startswith(b'data: {"n": ') and response.content.endswith(b'}\n'):
            number = int(response.content[len(b'data: {"n": '):-2])
        check(
            response is not None and STREAM_SECONDS <= took < STREAM_SECONDS + STREAM_OVERRUN_SECONDS
            and response.content.count(b'\n') == 1 and 0 < number <= last and last - number <= 3,
            f'7b. 이벤트가 계속 와도 {STREAM_SECONDS:g}초 근처에서 끝나고 그때까지의 마지막 이벤트를 받음 '
            f'({took:.1f}초, 받은 이벤트 {number}번, 서버가 보낸 마지막 {last}번, {error})',
        )

        response, error, took = stream_get(streaming, f'{base}silent')
        check(
            response is None and isinstance(error, ReadTimeout)
            and STREAM_TIMEOUT_SECONDS * 0.9 <= took < STALL_SECONDS,
            f'7c. 첫 이벤트 전에 {STREAM_TIMEOUT_SECONDS:g}초 넘게 멈추면 ReadTimeout ({took:.1f}초, {error or response})',
        )

        response, error, took = stream_get(streaming, f'{base}one-stall')
        check(
            response is not None and response.content == b'data: {"n": 1}\n'
            and STREAM_TIMEOUT_SECONDS * 0.9 <= took < STALL_SECONDS,
            f'7d. 이벤트 하나 뒤 {STREAM_TIMEOUT_SECONDS:g}초 넘게 멈추면 그 이벤트로 끝남 ({took:.1f}초, {response.content if response else error!r})',
        )

        response, error, took = stream_get(streaming, f'{base}one-drop')
        check(
            response is None and error is not None and not isinstance(error, ReadTimeout),
            f'7e. 이벤트 하나 뒤 서버가 연결을 끊으면 일부 이벤트를 성공으로 돌려주지 않고 전송 오류로 실패 '
            f'({took:.1f}초, {response.content if response else None!r}, {error!r})',
        )

        response, error, _ = stream_get(streaming, f'{base}big')
        expected = b'data: ' + BIG_EVENT_TEXT + b'\n'
        check(
            response is not None and response.content == expected,
            f'7f. 1MB 넘는 이벤트({len(BIG_EVENT_TEXT)}바이트)를 조각으로 나눠 받아 글자가 그대로 '
            f'({len(response.content) if response else error!r}바이트)',
        )


def check_credentials() -> None:
    entry = Site(EntryHandler)
    origin = f'http://{urlsplit(entry.url).netloc}'
    api = Site(type('CredentialsApi', (CredentialsApiHandler,), {'allowed_origin': origin}))
    cookies: dict[str, str] = {}
    # 첫 화면이 서버 쿠키(sid)를 심고, 127.0.0.1 의 쿠키는 포트가 달라도 같은 호스트라 API 사이트로 갈 수 있다. 실리느냐는 fetch 의
    # credentials 가 정한다 (같은 출처만 'same-origin', 다른 출처에도 'include')
    modes: tuple[Literal['same-origin', 'include'], ...] = ('same-origin', 'include')
    for mode in modes:
        options = BrowserOptions(
            source=f'browser-check-{mode}', entry_url=entry.url, ready_cookie=READY_COOKIE, fetch_credentials=mode,
        )
        with allow_local_network(), Client(transport=BrowserTransport(options), timeout=20) as checker:
            response = checker.get(f'{api.url}api/who', headers={PREFLIGHT_HEADER: '1'})
        cookies[mode] = response.json().get('cookie', '') if response.status_code == 200 else f'HTTP {response.status_code}'
    check(
        'sid=abc' not in cookies['same-origin'] and not cookies['same-origin'].startswith('HTTP'),
        f"8. 기본값('same-origin')은 다른 출처 API 에 쿠키를 싣지 않음 ({cookies['same-origin']!r})",
    )
    check(
        'sid=abc' in cookies['include'] and f'{READY_COOKIE}=1' in cookies['include'],
        f"8. fetch_credentials='include' 는 다른 출처 API 에 쿠키를 실음 ({cookies['include']!r})",
    )


def first_page_client(source: str, entry: Site, limiter: RateLimiter, **options: Any) -> Client:
    """9·12·13·15·16번 확인의 클라이언트. 우리 요청은 세지 않고(훅 없음) 첫 화면이 센 수만 `limiter` 에 남는다."""
    transport = BrowserTransport(BrowserOptions(
        source=source, entry_url=entry.url, ready_cookie=READY_COOKIE, rate_limiter=limiter, **options,
    ))
    return Client(transport=transport, timeout=20)


def new_limiter(source: str) -> RateLimiter:
    return RateLimiter(source, RateLimitPolicy(min_interval=0, max_per_minute=1000))


def check_first_page_options() -> None:
    # 기본값 대조 (429 가 없는 첫 화면): 요청이 끝나면 빈 페이지라 tick 이 멈추고, 정적 파일은 막히고, 첫 화면 API 요청은 센다
    control = Site(FirstPageHandler)
    control_limiter = new_limiter('browser-check-first-default')
    with first_page_client('browser-check-first-default', control, control_limiter) as checker:
        checker.get(f'{control.url}api/data')
        time.sleep(0.3)
        ticks = control.counts.get('/api/tick', 0)
        time.sleep(TICK_WAIT_SECONDS)
        control_ticks = control.counts.get('/api/tick', 0) - ticks
    control_recorded = control_limiter.status()['requests_last_minute']

    # 기본값 대조 (하위 요청이 429): 요청 제한이 쿨다운을 걸고 명령이 멈춘다
    limited = Site(type('LimitedFirstPage', (FirstPageHandler,), {'limited': True}))
    limited_limiter = new_limiter('browser-check-first-429')
    stopped = False
    try:
        with first_page_client('browser-check-first-429', limited, limited_limiter) as checker:
            checker.get(f'{limited.url}api/data')
    except RateLimitedError:
        stopped = True
    limited_cooldown = limited_limiter.status()['cooldown_remaining_seconds']

    # 새 설정을 모두 켠 첫 화면 (429 하위 요청 포함)
    page = Site(type('LimitedFirstPage', (FirstPageHandler,), {'limited': True}))
    page_limiter = new_limiter('browser-check-first-options')
    options = {
        'keep_cookies': False, 'stay_on_entry_page': True, 'count_entry_requests': False, 'block_entry_resources': False,
        'entry_settle_seconds': ENTRY_SETTLE_SECONDS,
    }
    with first_page_client('browser-check-first-options', page, page_limiter, **options) as checker:
        response = checker.get(f'{page.url}api/data')
        time.sleep(0.3)
        ticks = page.counts.get('/api/tick', 0)
        time.sleep(TICK_WAIT_SECONDS)
        page_ticks = page.counts.get('/api/tick', 0) - ticks
    recorded = page_limiter.status()['requests_last_minute']
    cooldown = page_limiter.status()['cooldown_remaining_seconds']

    check(
        response.status_code == 200 and page_ticks >= 3 and control_ticks <= 1,
        f'9. stay_on_entry_page=True 는 요청이 끝난 뒤에도 첫 화면이 살아 있음 (tick 이 {TICK_WAIT_SECONDS:g}초 동안 {page_ticks}번, '
        f'기본값은 {control_ticks}번, HTTP {response.status_code})',
    )
    check(
        recorded == 0 and control_recorded >= 3 and page.counts.get('/api/sub-a', 0) >= 1,
        f'12. count_entry_requests=False 는 첫 화면이 보낸 API 요청을 요청 제한에 세지 않음 (기록 {recorded}건, 서버가 받은 하위 요청 '
        f"{page.counts.get('/api/sub-a', 0)}건, 기본값은 기록 {control_recorded}건)",
    )
    check(
        response.status_code == 200 and cooldown == 0 and page.counts.get('/api/limited') == 1
        and stopped and limited_cooldown >= 1,
        f"12. 하위 요청이 받은 429 로 쿨다운을 걸지 않고 요청이 성공함 (쿨다운 {cooldown}초, 서버가 429 를 준 요청 "
        f"{page.counts.get('/api/limited')}건, 기본값은 멈춤 {stopped}·쿨다운 {limited_cooldown}초)",
    )
    reached = (page.counts.get('/static/style.css', 0), page.counts.get('/static/pic.png', 0))
    blocked = (control.counts.get('/static/style.css', 0), control.counts.get('/static/pic.png', 0))
    check(
        all(reached) and not any(blocked),
        f'13. block_entry_resources=False 는 스타일시트·이미지 요청이 서버에 닿음 (스타일시트·이미지 {reached}, 기본값 {blocked})',
    )
    settled = page.times['data'] - page.times['entry']
    unsettled = control.times['data'] - control.times['entry']
    check(
        settled >= ENTRY_SETTLE_SECONDS and settled - unsettled >= ENTRY_SETTLE_SECONDS * 0.5,
        f'15. entry_settle_seconds={ENTRY_SETTLE_SECONDS:g} 만큼 첫 요청이 늦게 나감 (첫 화면 문서 뒤 {settled:.1f}초, 기본값 {unsettled:.1f}초)',
    )


def check_entry_errors() -> None:
    other = Site(LoginHandler)
    cases = {
        'empty-403': {},
        'empty-429': {},
        'empty-503': {},
        'redirect': {'other_url': other.url},
    }
    # 경우마다 (에러, 그 경우가 끝난 직후 남은 쿨다운 초). 쿨다운은 그 자리에서 잰다. 네 경우를 다 돌린 뒤 재면 뒤 경우들이 Chromium 을
    # 띄우는 시간만큼 줄어, 느린 macOS 러너에서 429 의 600초 쿨다운이 588초로 재여 590초 기준에 걸렸다 (2026-11-24 CI)
    results: dict[str, tuple[BaseException | None, int]] = {}
    for mode, attrs in cases.items():
        site = Site(type('EmptyEntry', (EmptyEntryHandler,), {'mode': mode, **attrs}))
        source = f'browser-check-entry-{mode}'
        limiter = new_limiter(source)
        caught: BaseException | None = None
        try:
            with first_page_client(source, site, limiter, entry_block_cooldown=True) as checker:
                checker.get(f'{site.url}api/data')
        except Exception as error:  # 종류를 아래에서 가른다
            caught = error
        results[mode] = (caught, limiter.status()['cooldown_remaining_seconds'])

    def describe(error: BaseException | None) -> str:
        return '예외 없음' if error is None else f'{type(error).__name__}: {str(error)[:120]}'

    error, cooldown = results['empty-403']
    check(
        isinstance(error, RateLimitedError) and error.reason == 'blocked'
        and error.details.get('status') == 403 and error.details.get('entry_page') is True and cooldown >= 1,
        f'16. 본문 없는 403 첫 화면을 차단 쿨다운으로 판정함 (쿨다운 {cooldown}초, {describe(error)})',
    )
    error, cooldown = results['empty-429']
    check(
        isinstance(error, RateLimitedError) and error.details.get('status') == 429 and cooldown >= 590,
        f'16. 본문 없는 429 첫 화면이 Retry-After 600 을 쿨다운에 반영함 (쿨다운 {cooldown}초, {describe(error)})',
    )
    error, _ = results['empty-503']
    check(
        isinstance(error, HTTPStatusError) and error.response.status_code == 503,
        f'16. 본문 없는 503 첫 화면을 첫 화면 HTTP 503 에러로 판정함 ({describe(error)})',
    )
    error, _ = results['redirect']
    message = error.message if isinstance(error, BaenggipyoError) else ''
    host = other.url.removeprefix('http://').rstrip('/')
    check(
        isinstance(error, BaenggipyoError) and error.code == 'UPSTREAM_ERROR' and host in message
        and not any(word in message for word in ('token', 'secret', '/login')),
        f'16. 다른 출처로 넘어간 첫 화면 에러 message 에 출처만 싣고 경로·쿼리는 싣지 않음 ({describe(error)})',
    )


def check_entry_cleanup() -> None:
    # 본문 없는 403 첫 화면은 goto 가 실패해 다른 갈래(`_launch` 의 except)로 가므로, 본문 있는 403 처럼 거부된 쿠키·기록 폴더를
    # 지우는지 따로 본다 (2026-11-24 리뷰: 이 갈래만 지우지 않았다)
    site = Site(type('EmptyEntry', (EmptyEntryHandler,), {'mode': 'empty-403'}))
    profile_source = 'browser-check-entry-cleanup-profile'
    folder = state_dir() / 'browser' / f'{profile_source}-profile'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'marker').write_text('old')
    with suppress(Exception), Client(transport=BrowserTransport(BrowserOptions(
        source=profile_source, entry_url=site.url, ready_cookie=READY_COOKIE, keep_cookies=False, persistent_profile=True,
    )), timeout=20) as checker:
        checker.get(f'{site.url}api/data')
    check(not folder.exists(), f'16. 본문 없는 403 첫 화면 뒤 기록 폴더가 지워짐 (남음 {folder.exists()})')

    cookie_source = 'browser-check-entry-cleanup-cookies'
    saved = state_dir() / 'browser' / f'{cookie_source}.json'
    # 30분이 지나 첫 화면을 다시 열게 되는 옛 쿠키 파일
    saved.write_text(json.dumps({'opened_at': time.time() - 3600, 'page_url': site.url, 'cookies': []}))
    with suppress(Exception), Client(transport=BrowserTransport(BrowserOptions(
        source=cookie_source, entry_url=site.url, ready_cookie=READY_COOKIE,
    )), timeout=20) as checker:
        checker.get(f'{site.url}api/data')
    check(not saved.exists(), f'16. 본문 없는 403 첫 화면 뒤 옛 쿠키 파일이 지워짐 (남음 {saved.exists()})')


def check_profile() -> None:
    site = Site(ProfileEntryHandler)
    source = 'browser-check-profile'
    folder = state_dir() / 'browser' / f'{source}-profile'

    def temp_folders() -> set[Path]:
        return set(Path(tempfile.gettempdir()).glob(f'baenggipyo-{source}-profile-*'))

    def profile_client() -> Client:
        options = BrowserOptions(
            source=source, entry_url=site.url, ready_cookie=READY_COOKIE, keep_cookies=False, persistent_profile=True,
            discard_statuses=frozenset({403}),
        )
        return Client(transport=BrowserTransport(options), timeout=20)

    before = temp_folders()
    with profile_client() as first:
        first.get(f'{site.url}api/data')
        with profile_client() as second:
            second.get(f'{site.url}api/data')
            during = temp_folders() - before
        after_second = temp_folders() - before
    kept = folder.exists()
    fresh = site.counts.get('seen none no', 0)
    check(
        fresh == 2 and len(during) == 1 and not after_second,
        f'10. 기록 폴더를 다른 transport 가 쓰는 동안 띄운 transport 는 이어 받지 못하고 임시 폴더를 쓰며 닫으면 지움 '
        f'(이전 값 없이 연 첫 화면 {fresh}번, 열려 있는 동안 임시 폴더 {len(during)}개, 닫은 뒤 {len(after_second)}개)',
    )

    site.times.clear()
    with profile_client() as third:
        data = third.get(f'{site.url}api/data')
        forbidden = third.get(f'{site.url}api/forbidden')
    inherited = site.counts.get('seen v1 yes', 0)
    check(
        kept and inherited == 1,
        f'10. 닫은 뒤에도 기록 폴더가 남아 다음 transport 가 localStorage·쿠키를 이어 받음 (폴더 남음 {kept}, 이어 받은 첫 화면 {inherited}번)',
    )
    planted, requested = site.times.get('planted'), site.times.get('data')
    check(
        data.status_code == 200 and planted is not None and requested is not None and requested >= planted,
        f'14. 기록 폴더에 이미 ready 쿠키가 있어도 첫 화면이 새로 심을 때까지 기다린 뒤 요청함 '
        f'(요청이 쿠키를 심은 뒤 {None if planted is None or requested is None else round(requested - planted, 2)}초에 나감)',
    )
    gone = not folder.exists()
    check(
        forbidden.status_code == 403 and gone,
        f'11. 우리 요청이 403 을 받으면 닫은 뒤 기록 폴더가 지워짐 (HTTP {forbidden.status_code}, 폴더 지워짐 {gone})',
    )


def browser_processes() -> dict[int, str]:
    """baenggipyo 가 띄운 Chromium·드라이버로 보이는 프로세스 (pid → 명령줄)."""
    if sys.platform == 'win32':
        command = [
            'powershell', '-NoProfile', '-Command',
            "Get-CimInstance Win32_Process | ForEach-Object { '{0} {1}' -f $_.ProcessId, $_.CommandLine }",
        ]
    else:
        command = ['ps', '-axo', 'pid=,command=']
    out = subprocess.run(command, capture_output=True, text=True, errors='replace', check=False).stdout
    found: dict[int, str] = {}
    for line in out.splitlines():
        pid, _, cmdline = line.strip().partition(' ')
        if pid.isdigit() and any(marker in cmdline for marker in BROWSER_PROCESS_MARKERS):
            found[int(pid)] = cmdline[:150]
    return found


def interrupt_child() -> None:
    """(따로 띄운 프로세스) 느린 첫 화면을 여는 중에 Ctrl+C 를 흉내 내고 걸린 시간을 알린다."""
    entry = Site(EntryHandler)
    fired: list[float] = []

    def fire() -> None:
        # 브라우저가 첫 화면을 요청한 뒤(Chromium 은 이미 떴다) 보낸다. 2분 안에 요청이 안 오면 보내지 않아 아래에서 실패한다
        if entry.slow_started.wait(120):
            time.sleep(0.5)
            fired.append(time.monotonic())
            _thread.interrupt_main()

    threading.Thread(target=fire, daemon=True).start()
    try:
        with client('browser-check-slow', f'{entry.url}slow') as slow:
            slow.get(f'{entry.url}api/data')
    except KeyboardInterrupt:
        # with 를 빠져나오며 클라이언트를 닫았다. 하던 브라우저 일을 기다리지 않아야 한다
        print(json.dumps({'closed_after': time.monotonic() - fired[0]}), flush=True)
        return
    print(json.dumps({'error': 'Ctrl+C 를 받기 전에 요청이 끝났다'}), flush=True)


def check_interrupt() -> None:
    before = browser_processes()
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, __file__, '--interrupt-child'], capture_output=True, text=True, errors='replace',
        timeout=SLOW_SECONDS + 120, check=False,
    )
    took = time.monotonic() - started
    results = [json.loads(line) for line in done.stdout.splitlines() if line.startswith('{')]
    closed_after = results[-1].get('closed_after') if results else None
    check(
        closed_after is not None and closed_after < INTERRUPT_LIMIT_SECONDS,
        f'4. Ctrl+C 뒤 {INTERRUPT_LIMIT_SECONDS:g}초 안에 끝남 '
        f'(Ctrl+C → 닫기까지 {closed_after if closed_after is None else round(closed_after, 2)}초, 프로세스 전체 {took:.1f}초)',
    )
    if closed_after is None:
        print(done.stdout[-2000:], done.stderr[-2000:], sep='\n', flush=True)

    deadline = time.monotonic() + LEFTOVER_WAIT_SECONDS
    leftover = {pid: cmd for pid, cmd in browser_processes().items() if pid not in before}
    while leftover and time.monotonic() < deadline:
        time.sleep(0.5)
        leftover = {pid: cmd for pid, cmd in browser_processes().items() if pid not in before}
    check(not leftover, f'4. 끝난 뒤 Chromium·드라이버가 남지 않음 (남은 프로세스 {len(leftover)}개)')
    for pid, cmd in leftover.items():
        print(f'     {pid} {cmd}', flush=True)


def main() -> None:
    if '--interrupt-child' in sys.argv:
        interrupt_child()
        return
    print(f'상태 폴더: {os.environ["BAENGGIPYO_STATE_DIR"]}', flush=True)
    entry = Site(EntryHandler)
    check_requests(entry)
    check_blocked(entry, Site(BlockedHandler))
    check_interrupt()
    check_async_requests(entry)
    check_request_counts()
    check_streams()
    check_credentials()
    check_first_page_options()
    check_profile()
    check_entry_errors()
    check_entry_cleanup()
    if failures:
        print(f'\n{len(failures)}개 실패', flush=True)
        sys.exit(1)
    print('\n모두 통과', flush=True)


if __name__ == '__main__':
    main()
