"""httpx 요청을 사이트 페이지를 연 브라우저 안의 `fetch` 로 보내는 transport.

httpx `Client`·`AsyncClient` 의 `transport` 만 바꾸므로 base_url·auth·요청 제한 훅·재시도는 그대로 돈다.
첫 요청 때 scrapling `StealthySession` 으로 위장한 Chromium 을 띄워 사이트 첫 화면(`entry_url`)을 실제로 연다.
사이트 JS 가 돌며 심은 쿠키와 페이지 주소(Referer)·UA·`sec-ch-ua` 를 브라우저가 그대로 붙여 요청한다.
TLS·HTTP/2 지문은 브라우저 것이라 일반 Chrome 과 같다. scrapling 은 그 위에 사이트 JS 의 봇 검사에 걸리는
흔적(`navigator.webdriver`, Web Worker 언어 불일치, 화면 크기 등)을 감춘다.

CLI 는 명령마다 새 프로세스라 브라우저도 매번 빈 쿠키로 뜬다. 그래서 명령이 끝날 때 쿠키를
`<state_dir>/browser/<source>.json` 에 남기고, 첫 화면을 연 지 `REFRESH_SECONDS` 안이면 다음 명령은 첫 화면을
다시 열지 않고 남긴 쿠키로 바로 요청한다. 첫 화면을 열 때 페이지가 스스로 부르는 사이트 API 는 요청 제한 훅을
거치지 않아 `RateLimiter.record` 로 따로 센다.

브라우저 일은 transport 마다 띄운 전용 스레드 하나에서 받은 차례대로 한다 (`_BrowserThread`). patchright 동기 API 는
asyncio 이벤트 루프 안에서 부를 수 없지만 이 스레드에는 루프가 돌지 않아, 동기 `BrowserTransport` 는 어느 스레드·루프에서
불려도 이 스레드에 일을 넘기고 기다린다. 비동기 `AsyncBrowserTransport` 는 동기 transport 를 감싸 같은 일을 넘기고 `await` 로
기다린다 (`AsyncBrowserTransport.handle_async_request`). 그래서 흐름(`_open_page`·요청 보내기)은 한 벌이고, 동시에 온 요청도 이
스레드가 차례로 해 잠금이 없다. CLI 와 매일 API 감시는 동기 transport 만 쓴다. 비동기 쪽은
CI 의 브라우저 확인(`scripts/ci/browser_check.py` 5번)이 가짜 사이트로 띄워 보고, 실제 사이트는 고친 사람이 `AsyncClient` 로
직접 불러 확인한다.
scrapling·patchright 는 브라우저를 띄울 때 함수 안에서 불러온다.
patchright 를 불러오는 데 0.1초쯤 걸려 (2026-11-09 로컬 실측) 브라우저를 쓰지 않는 명령까지
느려지지 않게 하려는 것이다. `urllib.request` 는 맨 위에서 불러온다. httpx 가 이미 불러오는 모듈이라(`httpx._auth`) 함수
안으로 옮겨도 CLI 가 빨라지지 않는다 (2026-11-10 로컬 `python -X importtime` 확인).
"""

import asyncio
import atexit
import base64
import errno
import json
import logging
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections.abc import (
    Callable,
    Iterable,
    Mapping,
    Sequence,
)
from concurrent.futures import (
    Future,
    wait,
)
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import (
    unquote,
    urlsplit,
)

from httpx import (
    AsyncBaseTransport,
    BaseTransport,
    ConnectError,
    HTTPStatusError,
    ProxyError,
    ReadTimeout,
    Request,
    Response,
    TransportError,
)

from baenggipyo.core.errors import BaenggipyoError
from baenggipyo.core.ratelimit import (
    RateLimitedError,
    RateLimiter,
    StateDirError,
    state_dir,
    write_json_atomic,
)

from typing import (
    IO,
    TYPE_CHECKING,
    Any,
    Literal,
    NamedTuple,
    TypeVar,
)


if sys.platform == 'win32':
    import msvcrt
else:
    import fcntl


if TYPE_CHECKING:
    from patchright.sync_api import (
        BrowserContext,
        CDPSession,
        Page,
        Route,
    )
    from patchright.sync_api import Response as PageResponse
    from scrapling.fetchers import StealthySession


logger = logging.getLogger(__name__)

_R = TypeVar('_R')

# 임시 기록 폴더를 쓰는 중임을 알리는 잠금 파일 이름. `_sweep_temp_profiles` 가 본다
TEMP_PROFILE_LOCK_NAME = '.baenggipyo-lock'
# 이 시간(초)보다 덜 지난 임시 기록 폴더는 치우지 않는다. 주인 프로세스가 끝난 뒤에도 Chromium 이 몇 초 동안 폴더에 쓴다
# (2026-11-24 실측: `atexit` 정리 뒤 Chromium 이 닫히며 폴더를 다시 만들었다). 막 만든 폴더(잠금 파일을 만들기 전)도 이 시간 동안은 건드리지 않는다
STALE_TEMP_PROFILE_SECONDS = 60

# transport 를 timeout 없이 만들었을 때, 요청에 timeout 이 없으면 쓰는 값 (초). BaseHTTPClient.timeout 기본값과 같다.
# BaseHTTPClient 는 자기 timeout 을 넘긴다 (`_browser_options`)
DEFAULT_TIMEOUT_SECONDS: float = 20.0
# 첫 화면 문서(DOMContentLoaded)를 기다리는 시간. 넘으면 EntryPageError(NETWORK_ERROR)
ENTRY_TIMEOUT_MS: float = 40_000
# 그 뒤 load 를 기다리는 시간. 넘어도 실패시키지 않고 그때까지 받은 쿠키로 넘어간다. load 는 사이트 밖 광고·분석 스크립트
# (구글 태그 매니저 등)까지 기다려, 그 호스트를 응답 없이 버리는 망에서는 사이트 API 가 멀쩡해도 첫 화면이 문서 제한 시간(40초)
# 뒤에야 실패한다 (2026-11-09 로컬 실측: www.googletagmanager.com 을 응답 없는 주소로 돌리니 40초 시간 초과)
ENTRY_LOAD_TIMEOUT_MS: float = 10_000
# 첫 화면 load 뒤 사이트 JS 가 자기 API 를 부르며 쿠키를 심는다. 쿠키가 load 뒤에 생기므로 기다린다.
# networkidle 은 쿠키가 0개일 때 끝나기도 해 기준으로 쓰지 않는다. 네이버 항공은 `NNB` 가 첫 화면을 연 지 1초 안에 생긴다 (2026-11-24 실측)
READY_COOKIE_TIMEOUT_SECONDS: float = 5.0
READY_COOKIE_POLL_MS: float = 200
# ready_cookie 가 생긴 뒤에도 쿠키가 이 시간 동안 더 바뀌지 않아야 다 심은 것으로 본다. 사이트는 쿠키를 몇백 ms 에 걸쳐 나눠
# 심을 수 있어, ready_cookie 만 보고 멈추면 뒤의 것을 놓친다
READY_COOKIE_SETTLE_SECONDS: float = 0.5
# 첫 화면에서 받은 쿠키를 다시 쓰는 시간. 이 안이면 다음 명령은 첫 화면을 다시 열지 않는다. baenggipyo 소스의 쿠키 수명은 재지 않았다
REFRESH_SECONDS: float = 1800
# 우리 요청이 이 상태를 받으면 남긴 쿠키를 버려 다음 명령이 첫 화면부터 연다 (거부된 방문자 쿠키를 이어 쓰지 않는다).
# 소스가 403 을 다른 뜻으로 쓰면 `discard_statuses` 로 바꾼다.
# 429 는 넣지 않는다. 요청량·IP 단위 차단이라 쿠키를 바꿔도 풀리지 않고, 버리면 쿨다운
# 뒤 첫 명령이 아직 막혀 있을지 모르는 사이트에 첫 화면 요청을 여러 건 한꺼번에 보낸다. 남겨 두면 요청 1건으로 풀렸는지 본다
DISCARD_STATE_STATUSES: frozenset[int] = frozenset({403})
# 첫 화면에서 받지 않는 정적 파일. 쿠키는 문서와 사이트 JS·API 응답으로 생겨 이것들을 막아도 같고 받는 요청 수만 줄어든다.
# 사이트 자체 API 는 막지 않는다. 막으면 사이트 JS 가 심는 쿠키가 생기지 않을 수 있다
BLOCKED_RESOURCE_TYPES: frozenset[str] = frozenset({'image', 'font', 'media', 'stylesheet'})
# 위 정적 파일 종류를 막는 CDP Fetch 패턴 (CDP 리소스 종류는 첫 글자가 대문자다). route 로 막으면 patchright 가 CORS 사전 요청을 서버에
# 보내지 않아(`UNROUTE_BEHAVIOR`) 첫 화면 JS 가 커스텀 헤더로 부르는 다른 출처 API 에 OPTIONS 가 빠진다.
# CDP Fetch 는 이 패턴에 맞는 요청만 멈춰, 나머지 요청과 사전 요청은 브라우저가 그대로 보낸다
# (2026-11-10 로컬 실측: route 면 OPTIONS 없이 GET 만, Fetch 면 OPTIONS → GET, 이미지·CSS 는 둘 다 막힘)
BLOCKED_FETCH_PATTERNS: tuple[dict[str, str], ...] = (
    *({'urlPattern': '*', 'resourceType': kind.capitalize(), 'requestStage': 'Request'} for kind in sorted(BLOCKED_RESOURCE_TYPES)),
)
# 요청 제한에 세는 페이지 요청 종류. 사이트가 JS 파일도 API 와 같은 호스트에서 주면 그것까지 세지 않는다
COUNTED_RESOURCE_TYPES: frozenset[str] = frozenset({'xhr', 'fetch'})
# fetch 가 바꿀 수 없거나 브라우저 값을 그대로 쓸 헤더 (UA·Origin·Referer·쿠키·언어는 연 페이지 기준으로 브라우저가 붙인다)
BROWSER_OWNED_HEADERS: frozenset[str] = frozenset({
    'accept-charset', 'accept-encoding', 'accept-language', 'access-control-request-headers',
    'access-control-request-method', 'connection', 'content-length', 'cookie', 'cookie2', 'date', 'dnt', 'expect',
    'host', 'keep-alive', 'origin', 'referer', 'set-cookie', 'te', 'trailer', 'transfer-encoding', 'upgrade',
    'user-agent', 'via',
})
BROWSER_OWNED_HEADER_PREFIXES: tuple[str, ...] = ('proxy-', 'sec-')
# 요청에 이 요청 extension(초, float)이 있으면 그 요청은 본문(SSE)을 브라우저 안에서 줄 단위로 읽어 마지막 `data:` 줄만 돌려준다
# (`extensions={STREAM_SECONDS_EXTENSION: 30.0}`). 헤더가 온 때부터 이 시간이 지나면 그때까지 받은 마지막 줄로 끝낸다 (`FETCH_SCRIPT`).
# 이벤트마다 그때까지의 결과 전체를 다시 보내는 스트림은 앞 이벤트를 버려도 되는데, 파이썬으로 받으면 그 본문을 모두 옮겨야 한다
STREAM_SECONDS_EXTENSION: str = 'baenggipyo_stream_seconds'
# 브라우저가 압축을 풀어 본문을 주므로, 남겨 두면 httpx 가 한 번 더 풀려다 깨진다
DECODED_RESPONSE_HEADERS: frozenset[str] = frozenset({'content-encoding', 'content-length', 'transfer-encoding'})
# 서버에 묻지 않고 탭을 띄울 때 돌려주는 빈 페이지 (`_fulfill_blank`). 아이콘을 data: 로 두어 favicon 요청이 나가지 않는다
BLANK_PAGE_HTML: str = '<link rel="icon" href="data:,">'
# 본문이 이보다 크면 evaluate 한 번에 돌려주지 않고 브라우저 안에 두었다가 이만큼씩 나눠 읽는다 (`_handle_request`). patchright 는
# evaluate 결과가 크면 넘기는 시간이 크기보다 훨씬 빨리 는다. 같은 본문(97MB, 네트워크 없이 브라우저 안에서 만든 것)을 한 번에 넘기면
# 44초, 1MB 씩이면 2.2초, 4MB 씩 2.7초, 16MB 씩 8.4초였다 (2026-11-11 로컬 실측). 작은 응답은 한 번에 돌려주는 쪽만 탄다
BODY_CHUNK_BYTES: int = 1 << 20
# 브라우저 setTimeout 이 받는 가장 긴 시간(ms). 이보다 길면 기다리지 않고 바로 울린다 (2026-11-12 로컬 실측: timeout 3,000,000초는
# 0초에 ReadTimeout, 2,147,483초는 정상). `BaseHTTPClient.timeout` 은 상한이 없어 `_fetch_arguments` 가 여기서 자르고, 에러
# 메시지의 초도 자른 값으로 적는다
MAX_TIMER_MS: int = 2**31 - 1
# credentials 는 `BrowserOptions.fetch_credentials` 로 정해 fetch 에 넘긴다. 기본값 'same-origin'(fetch 기본값)은 같은 출처에만
# 쿠키를 싣고 다른 출처에는 싣지 않는다. 'include' 는 다른 출처에도 싣는다. 다른 출처 응답이 `Access-Control-Allow-Origin: *` 를
# 허용하면 'include' 는 브라우저가 거절하므로(2026-11-09 로컬 실측), 사이트 API 가 첫 화면과 다른 출처이고 웹이 쿠키를 싣는 소스만
# 'include' 로 둔다. 그 서버는 사전 요청에 `Access-Control-Allow-Credentials: true` 와 첫 화면 출처를 답해야 한다.
# redirect 는 브라우저 안에서 따라간다. 'manual' 이면 fetch 가 상태 0 인 opaqueredirect 를 줘 상태·Location 을 볼
# 수 없어서, httpx 클라이언트의 follow_redirects 대로 httpx 가 따라가게 둘 수 없다. 같은 출처 redirect 는 사이트 웹처럼 따라가고
# (끝이 JSON 이 아니면 PARSE_ERROR), CORS 를 허용하지 않는 다른 출처(로그인·보안 확인 화면)로 넘기면 fetch 가 실패해
# 첫 응답(3xx)을 `_BlockedResponses` 로 돌려준다. 그래서 따라가지 않던 예전 httpx 처럼 UPSTREAM_ERROR 가 된다.
# blob 을 base64 로 바꾸는 JS 함수 (FileReader 가 만든 data URL 의 쉼표 뒤). `FETCH_SCRIPT`·`BODY_SLICE_SCRIPT` 가 함께 쓴다.
# 빈 blob 도 `data:application/octet-stream;base64,` 라 빈 문자열이 된다 (2026-11-12 로컬 실측)
BLOB_BASE64_SCRIPT: str = """(blob) => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(reader.result.slice(reader.result.indexOf(',') + 1));
  reader.onerror = () => reject(reader.error);
  reader.readAsDataURL(blob);
})"""
# requestUrl 은 브라우저가 정리한 요청 주소로, CDP 이벤트의 요청 주소와 맞춰 보는 데 쓴다.
# timeoutMs 는 응답 헤더가 올 때까지와 본문 조각 사이 간격의 제한이다 (`_fetch_arguments`). 본문을 조각마다 읽어 그 사이를 잰다.
# 헤더가 늦으면 fetch 를 끊어 AbortError 다. 본문이 멈추면 이미 받은 상태·헤더에 error 'BodyTimeout' 을 붙여 돌려준다
# (`_to_response` 가 3xx 이상이면 본문 없는 응답으로 만든다).
# streamMs 가 null 이 아니고 응답이 2xx 면 본문을 끝까지 모으지 않고 줄 단위로 읽어 마지막 `data:` 줄(+ '\n')만 본문으로 삼는다
# (`STREAM_SECONDS_EXTENSION`). 헤더가 온 때부터 streamMs 가 지난 뒤 줄이 완성되면 읽기를 멈추고 그때까지의 마지막 줄로 끝낸다.
# 다음 조각이 timeoutMs 안에 오지 않으면 받은 줄이 있을 때만 그것으로 끝내고, 없으면 BodyTimeout 이다. 읽다 연결이 끊기면 받은 줄을
# 쓰지 않고 에러를 그대로 올린다 (끊긴 스트림의 일부 결과를 쓰지 않는다). 2xx 가 아니면 스트림이 아니라 지금처럼 끝까지 읽는다.
# 본문이 inlineBytes 보다 크면 본문 대신 bodyId 를 돌려주고 본문은 이 evaluate 세계의 전역(`__baenggipyoBodies`)에 둔다
# (`BODY_SLICE_SCRIPT` 로 읽고 `BODY_DROP_SCRIPT` 로 지운다). patchright 의 evaluate 는 사이트 JS 와 다른 격리된 세계에서 돌아
# 사이트 JS 에는 이 전역이 보이지 않는다 (2026-11-11 로컬 실측: evaluate 로 둔 전역이 페이지 main world 에서는 undefined)
FETCH_SCRIPT: str = """async ({url, method, headers, body, credentials, timeoutMs, streamMs, inlineBytes}) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let requestUrl = url;
  const readWithin = (reader, ms) => new Promise((resolve, reject) => {
    const idle = setTimeout(() => resolve(null), ms);
    reader.read().then(
      (chunk) => { clearTimeout(idle); resolve(chunk); },
      (error) => { clearTimeout(idle); reject(error); },
    );
  });
  const readAll = async (reader) => {
    const chunks = [];
    while (true) {
      const chunk = await readWithin(reader, timeoutMs);
      if (chunk === null) {
        reader.cancel().catch(() => {});
        return null;
      }
      if (chunk.done) return new Blob(chunks);
      chunks.push(chunk.value);
    }
  };
  const readLastEvent = async (reader) => {
    const deadline = Date.now() + streamMs;
    const decoder = new TextDecoder('utf-8');
    const finish = (last) => new Blob([last === null ? '' : last + '\\n']);
    let pending = '';
    let last = null;
    const take = (raw) => {
      const line = raw.endsWith('\\r') ? raw.slice(0, -1) : raw;
      if (line.startsWith('data:')) last = line;
    };
    while (true) {
      const chunk = await readWithin(reader, timeoutMs);
      if (chunk === null) {
        reader.cancel().catch(() => {});
        return last === null ? null : finish(last);
      }
      if (chunk.done) {
        pending += decoder.decode();
        if (pending !== '') take(pending);
        return finish(last);
      }
      const parts = decoder.decode(chunk.value, {stream: true}).split('\\n');
      if (parts.length === 1) {
        pending += parts[0];
        continue;
      }
      const lines = [pending + parts[0], ...parts.slice(1, -1)];
      pending = parts[parts.length - 1];
      for (const line of lines) {
        take(line);
        if (Date.now() > deadline) {
          reader.cancel().catch(() => {});
          return finish(last);
        }
      }
    }
  };
  const base64 = """ + BLOB_BASE64_SCRIPT + """;
  try {
    requestUrl = new URL(url, location.href).href;
    const init = {method, headers, redirect: 'follow', credentials, signal: controller.signal};
    if (body !== null) init.body = Uint8Array.from(atob(body), (c) => c.charCodeAt(0));
    const response = await fetch(url, init);
    clearTimeout(timer);
    const meta = {requestUrl, url: response.url, status: response.status, headers: [...response.headers]};
    const streaming = streamMs !== null && response.status >= 200 && response.status < 300;
    const blob = !response.body ? new Blob([])
      : streaming ? await readLastEvent(response.body.getReader()) : await readAll(response.body.getReader());
    if (blob === null) return {...meta, error: 'BodyTimeout'};
    if (blob.size <= inlineBytes) return {...meta, body: await base64(blob)};
    const bodies = globalThis.__baenggipyoBodies || (globalThis.__baenggipyoBodies = new Map());
    globalThis.__baenggipyoBodyCount = (globalThis.__baenggipyoBodyCount || 0) + 1;
    const bodyId = String(globalThis.__baenggipyoBodyCount);
    bodies.set(bodyId, blob);
    return {...meta, bodyId, size: blob.size};
  } catch (error) {
    return {requestUrl, error: error.name, message: String(error.message || error)};
  } finally {
    clearTimeout(timer);
  }
}"""
# `FETCH_SCRIPT` 가 브라우저 안에 둔 본문(bodyId)의 [start, end) 바이트를 base64 로 돌려준다 (`_handle_request`). 본문이 없으면(그 사이
# 격리 세계가 바뀜) 이유가 보이게 던져 `ConnectError` 메시지에 실린다. 그냥 읽으면 `Cannot read properties of undefined` 로만
# 보였다 (2026-11-12 로컬 실측. 같은 실측에서 3MB 본문을 세 번에 나눠 읽는 동안 전역은 유지됐다)
BODY_SLICE_SCRIPT: str = """async ([bodyId, start, end]) => {
  const blob = globalThis.__baenggipyoBodies?.get(bodyId);
  if (!blob) throw new Error(`브라우저에 둔 응답 본문(${bodyId})이 없습니다`);
  const base64 = """ + BLOB_BASE64_SCRIPT + """;
  return base64(blob.slice(start, end));
}"""
BODY_DROP_SCRIPT: str = """(bodyId) => { globalThis.__baenggipyoBodies?.delete(bodyId); }"""


# 브라우저가 프록시에 닿지 못했거나 프록시가 CONNECT 를 거절했을 때의 Chromium 네트워크 오류(앞부분). httpx 의 ProxyError 처럼
# 다시 보내도 같아 재시도하지 않는다. 첫 화면(`_entry_error`, patchright 에러 문구)과 우리 요청(`_to_response`, CDP
# `loadingFailed.errorText`)에 같이 쓴다 (2026-11-09 실측: 닫힌 포트를 프록시로 주면 둘 다 net::ERR_PROXY_CONNECTION_FAILED)
PROXY_NET_ERRORS: tuple[str, ...] = ('net::ERR_PROXY_', 'net::ERR_TUNNEL_CONNECTION_FAILED', 'net::ERR_NO_SUPPORTED_PROXIES')
# 그중 프록시가 이 사이트로의 연결만 거절한 것. 나머지(프록시에 닿지 못함·쓸 수 없는 프록시)는 모든 사이트가 같다
SITE_PROXY_NET_ERRORS: tuple[str, ...] = ('net::ERR_TUNNEL_CONNECTION_FAILED',)
# Chromium 이 받는 프록시 스킴과, curl·httpx 에서 쓰던 스킴을 Chromium 스킴으로 바꾸는 표 (`_proxy_settings`)
BROWSER_PROXY_SCHEMES: frozenset[str] = frozenset({'http', 'https', 'socks4', 'socks5'})
PROXY_SCHEME_ALIASES: dict[str, str] = {'socks5h': 'socks5', 'socks4a': 'socks4'}
# 프록시 오류의 hint. 환경 변수가 없으면 Chromium 이 OS 프록시 설정(PAC 포함)을 쓴다. NO_PROXY=* 는 OS 프록시도 건너뛴다
# (`_proxy_settings`). CLI `handle_errors` 가 ProxyError 에, `_entry_error` 가 첫 화면의 사이트별 거절에 붙인다
PROXY_HINT: str = (
    '프록시 설정을 확인하세요: HTTPS_PROXY·ALL_PROXY 환경 변수, 없으면 OS 프록시 설정(VPN·회사 PAC 포함). '
    '프록시 없이 바로 연결하려면 NO_PROXY=* 로 실행하세요 (OS 프록시도 건너뜁니다)'
)
# 브라우저가 첫 화면의 인증서를 믿지 못했을 때의 hint. Chromium 은 SSL_CERT_FILE·SSL_CERT_DIR 을 보지 않아 회사망·보안
# 프로그램의 인증서는 OS 저장소에 있어야 한다. 다시 열어도 같아 재시도하지 않는다 (2026-11-10 로컬 실측: 테스트 CA 를
# SSL_CERT_FILE 로 주면 httpx 는 200, 브라우저는 net::ERR_CERT_AUTHORITY_INVALID 로 Chromium 을 세 번 띄운 뒤
# '네트워크 상태를 확인하라'로 끝났다)
CERT_HINT: str = (
    '회사망이나 보안 프로그램이 HTTPS 를 검사하면 그 인증서를 OS 에 설치해야 브라우저가 믿습니다 (Windows 인증서 저장소, '
    'macOS 키체인, Linux 는 ~/.pki/nssdb). SSL_CERT_FILE 은 쓰지 않습니다. 다시 불러도 같으니 사용자에게 알리세요'
)
# 브라우저가 인증서를 믿지 못한 Chromium 네트워크 오류(앞부분). 첫 화면(`_entry_error`)과 우리 요청(`_to_response`)에 같이 쓴다
CERT_NET_ERROR: str = 'net::ERR_CERT_'
# 본문 없는 4xx·5xx 첫 화면 문서에서 Chromium 이 `page.goto` 를 실패시키는 오류. 그 전에 main frame 문서의 `response` 이벤트가
# 상태와 Retry-After 와 함께 와서 `_launch` 가 그 값으로 첫 화면 판정(`finish_entry`)을 한다
# (2026-11-24 로컬 실측: `Content-Length: 0` 의 403·429·503)
HTTP_RESPONSE_CODE_NET_ERROR: str = 'net::ERR_HTTP_RESPONSE_CODE_FAILURE'
# fetch 가 실패했을 때 그 요청의 CDP 이벤트(`_BlockedResponses`)를 기다리는 시간. evaluate 결과보다 10ms 안쪽으로 늦게 왔다
# (2026-11-09 로컬 실측: 동기·비동기 각 30번, 바로 보면 6·10번이 아직 없었고 10ms 한 번 기다리면 모두 왔다)
BLOCKED_RESPONSE_WAIT_SECONDS: float = 1.0
BLOCKED_RESPONSE_POLL_MS: float = 10
# 다시 열어도 같은 첫 화면 실패(`_BrowserState.remember_if_fatal`)를 소스별로 기억하는 시간. 같은 명령 안에서 실패한 뒤
# 새로 만든 같은 소스 클라이언트가 브라우저를 다시 띄워 첫 화면 요청을 또 보내고 40초를 또 기다리지
# 않게 한다. 라이브러리로 오래 쓰는 프로세스는 이 시간이 지나면 다시 연다
FAILED_ENTRY_MEMORY_SECONDS: float = 60
# 첫 화면 문서가 이 상태면 사이트가 이 접속(IP·봇)을 막은 것으로 보고 에러에 `blocked: true` 를 붙인다
# (`_BrowserState.finish_entry`). 업데이트로 풀리지 않으니 에이전트는 업데이트를 권하지 않고, 막힘과 고장을 가르는 호출부(감시 등)는
# hint 문구가 아니라 이 표시를 본다. 그 밖의 4xx(404 등)는 첫 화면 주소가 바뀌었을 수 있다.
ENTRY_BLOCKED_STATUSES: frozenset[int] = frozenset({401, 403, 451})
# (소스, proxy, 환경 변수 프록시, verify) → (실패 시각, 에러). 프록시·인증서 설정이 다른 클라이언트에는 앞 실패를 다시 내지
# 않는다 (`_BrowserState._failure_key`)
_FailureKey = tuple[str, str | None, tuple[tuple[str, str], ...] | None, bool]
_failed_entries: dict[_FailureKey, tuple[float, Exception]] = {}


class EntryOpenError(Exception):
    """브라우저를 띄워 사이트 첫 화면을 여는 단계에서 실패했다는 표식. 같은 클라이언트의 본 요청도 같은 단계부터 다시 한다.

    첫 화면 단계의 실패만 따로 다룰 곳이 잡는다 (스카이스캐너 재시도 술어 `should_retry_skyscanner` 가 API 응답 500 과 가른다). 재시도 술어와 CLI 에러 매핑은 원래 종류대로 다루도록 실제 에러는 이것과
    httpx·baenggipyo 에러를 함께 물려받는다 (`EntryConnectError`, `EntryStatusError`, `EntryPageError`).
    """


class EntryConnectError(ConnectError, EntryOpenError):
    """첫 화면을 여는 단계의 연결 실패. ConnectError 라 재시도한다."""


class EntryStatusError(HTTPStatusError, EntryOpenError):
    """첫 화면 문서가 5xx(또는 요청 제한을 끈 429)였다. HTTPStatusError 라 재시도한다.

    API 응답이 아니므로 상태 코드에 소스 고유 뜻을 붙이는 호출부는 `EntryOpenError` 를 먼저 걸러 낸다. 거르지 않으면
    사이트 장애를 입력 오류로 안내하고 재시도도 하지 않을 수 있다.
    """


class EntryPageError(BaenggipyoError, EntryOpenError):
    """사이트 첫 화면을 제대로 열지 못했다 (시간 초과·프록시가 사이트를 거절함·인증서 오류는 NETWORK_ERROR, 차단 응답·다른 출처로 넘어감·60초 넘게 기다리라는 5xx 는 UPSTREAM_ERROR).

    재시도하지 않는다. 다시 열면 브라우저를 새로 띄우고 첫 화면 요청을 여러 건 또 보내며, 시간 초과면 40초를 또 기다린다.
    소스별로 `FAILED_ENTRY_MEMORY_SECONDS` 동안 기억한다 (`_BrowserState.remember_if_fatal`). 전송 오류(`HTTPError`)가 아닌
    baenggipyo 에러라 재시도 술어가 보지 않고 `handle_errors` 가 코드·hint·details 를 그대로 낸다. 같은 클라이언트의 본 요청도
    같은 에러로 끝나므로 결과는 같다.
    """


class BrowserUnavailableError(BaenggipyoError):
    """Chromium 을 띄우지 못했다 (BROWSER_UNAVAILABLE). 설치·시스템 라이브러리·드라이버 문제라 모든 소스가 같이 실패한다."""

    def __init__(self, message: str, hint: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(code='BROWSER_UNAVAILABLE', message=message, hint=hint, details=details)


class CorsBlockedError(TransportError):
    """브라우저가 CORS 로 응답을 막아 읽을 수 없다 (`_to_response`).

    다른 출처 응답이 2xx 인데 `Access-Control-Allow-Origin` 이 없거나, CORS 사전 요청(OPTIONS)이 거절돼 본 요청이 나가지
    못했을 때다. 3xx·4xx·5xx 응답은 본문 없이도 상태로 다룰 수 있어 `_BlockedResponses` 의 상태로 응답을 만들지만, 2xx 를
    빈 본문으로 넘기면 빈 결과나 PARSE_ERROR 가 되어 원인이 가려진다. 사이트의 CORS 설정이 바뀐 것이라 다시 보내도 같아
    재시도하지 않는다 (재시도 술어의 `RETRYABLE_TRANSPORT_ERRORS` 에 들지 않는다). CLI 는 UPSTREAM_ERROR 로 낸다 (`handle_errors`).

    AGENTS.md 의 '새 에러는 BaenggipyoError 로' 를 일부러 따르지 않는다. transport 가 내는 전송 오류라 httpx `TransportError` 로
    두고, 재시도 술어(`RETRYABLE_TRANSPORT_ERRORS`)와 `handle_errors` 가 httpx 예외로 갈라 코드·hint 를 붙인다 (`ProxyError` 와 같다).
    """


class CertificateError(TransportError):
    """브라우저가 우리 요청의 사이트 인증서를 믿지 못했다 (`net::ERR_CERT_*`, `_to_response`).

    회사망·보안 프로그램이 HTTPS 를 검사하는데 그 인증서가 OS 에 없으면 다시 보내도 같아 재시도하지 않는다
    (`RETRYABLE_TRANSPORT_ERRORS` 에 들지 않는다). 첫 화면의 인증서 오류는 `_entry_error` 가 `EntryPageError` 로 내지만,
    남긴 쿠키로 띄운 30분 동안은 첫 화면을 서버에 묻지 않아 우리 요청에서 처음 드러난다 (2026-11-10 로컬 실측: ConnectError 로
    3번 보낸 뒤 '네트워크 상태를 확인하라'로 끝났다). `CorsBlockedError`·`ProxyError` 처럼 httpx 전송
    오류로 두고, CLI 는 NETWORK_ERROR 와 `CERT_HINT` 를 붙인다 (`handle_errors`).
    """


def _playwright_error() -> type[Exception]:
    """patchright 에러 클래스. 동기·비동기 API 가 같은 클래스를 쓴다."""
    # 모듈 docstring 대로 브라우저를 쓸 때만 불러온다
    from patchright.sync_api import Error
    return Error


def _playwright_timeout_error() -> type[Exception]:
    """patchright 시간 초과 에러 클래스. 동기·비동기 API 가 같은 클래스를 쓴다."""
    # 모듈 docstring 대로 브라우저를 쓸 때만 불러온다
    from patchright.sync_api import TimeoutError as PlaywrightTimeoutError
    return PlaywrightTimeoutError


def _first_line(error: Exception) -> str:
    return (str(error).splitlines() or [''])[0]


def _missing_libraries_hint() -> str:
    return (
        f"리눅스는 Chromium 이 쓰는 시스템 라이브러리가 없으면 'sudo {sys.executable} -m patchright install-deps chromium' "
        "을 한 번 실행한 뒤 'baenggipyo install-browser' 로 확인하세요"
    )


def _launch_error(error: Exception) -> BrowserUnavailableError:
    """Chromium 을 띄우다 난 에러. 모든 소스가 같이 실패하고 다시 띄워도 같아 재시도하지 않는 baenggipyo 에러로 낸다."""
    if isinstance(error, OSError):
        # patchright 드라이버(node)를 실행하지 못했다. Chromium·시스템 라이브러리가 아니라 설치된 baenggipyo 쪽 문제다
        return BrowserUnavailableError(
            message=f'브라우저 드라이버(patchright)를 실행하지 못했습니다: {error}',
            hint=(
                'baenggipyo 설치가 깨졌거나 보안 프로그램이 드라이버 실행을 막았을 수 있습니다. baenggipyo 를 다시 설치하고, 같은 에러면 '
                'message 의 원인을 사용자에게 알리세요'
            ),
        )
    if isinstance(error, _playwright_timeout_error()):
        # 시스템 라이브러리가 아니라 늦게 뜬 것이다. 띄우기 제한 시간(Playwright 기본 3분)을 다시 기다리지 않게 재시도는 하지 않는다
        return BrowserUnavailableError(
            message=f'Chromium 이 제시간에 뜨지 않았습니다: {_first_line(error)}',
            hint=(
                '컴퓨터가 바쁘거나 보안 프로그램이 Chromium 첫 실행을 검사 중일 수 있습니다. 잠시 뒤 한 번 다시 실행하고, 같으면 '
                'message 의 원인을 사용자에게 알리세요'
            ),
            details={'reason': 'LAUNCH_TIMEOUT'},
        )
    # Chromium 이 없거나 시스템 라이브러리가 없어 못 띄운 에러면 설치 안내가 든 baenggipyo 에러를 내고, 아니면 아래 일반 에러를 낸다
    text = str(error)
    if "Executable doesn't exist" in text:
        return BrowserUnavailableError(
            message='브라우저로 요청할 수 없습니다: Chromium 이 설치되어 있지 않습니다',
            hint="'baenggipyo install-browser' 로 Chromium 을 설치하세요 (baenggipyo 를 업데이트한 뒤에도 한 번 실행합니다)",
        )
    # Playwright 계열이 시스템 라이브러리가 없을 때 내는 문구 (사전 검사 문구, 실행 파일의 동적 링크 실패)
    if 'missing dependencies' in text or 'error while loading shared libraries' in text:
        return BrowserUnavailableError(
            message='브라우저로 요청할 수 없습니다: Chromium 에 필요한 시스템 라이브러리가 없습니다',
            hint=_missing_libraries_hint(),
        )
    return BrowserUnavailableError(
        message=f'Chromium 을 띄우지 못했습니다: {_first_line(error)}',
        hint=_missing_libraries_hint() if sys.platform.startswith('linux') else (
            "'baenggipyo install-browser' 로 설치와 실행을 확인하세요. 같은 에러면 message 의 원인"
            '(보안 프로그램이 실행을 막았는지 등)을 사용자에게 알리세요'
        ),
    )


def _proxy_error(net_error: str, request: Request | None = None) -> ProxyError:
    """Chromium 프록시 오류를 `ProxyError` 로 만든다. 프록시가 이 사이트 연결만 거절한 것(`SITE_PROXY_NET_ERRORS`)은 메시지로 가른다."""
    site_refused = any(code in net_error for code in SITE_PROXY_NET_ERRORS)
    reason = '프록시가 이 사이트 연결을 거절했습니다' if site_refused else '브라우저가 프록시로 연결하지 못했습니다'
    return ProxyError(f'{reason}: {net_error}', request=request)


def _entry_error(error: Exception, entry_url: str, opening_entry: bool) -> Exception:
    """첫 화면 탭을 준비하다 난 patchright 에러를 재시도 술어가 아는 에러로 바꾼다. 프록시에 닿지 못한 것(`ProxyError`) 말고는 첫 화면 단계 실패(`EntryOpenError`)다.

    사이트 첫 화면 문서를 기다리다 난 시간 초과(`opening_entry`)는 `EntryPageError`, 프록시에 닿지 못한 것은
    `ProxyError`, 프록시가 이 사이트만 거절한 것은 `EntryPageError`(NETWORK_ERROR, 프록시 hint), 인증서를 믿지
    못한 것(`net::ERR_CERT_*`)은 `EntryPageError`(NETWORK_ERROR, `CERT_HINT`)로 낸다. 모두 다시 열어도 같아 재시도하지
    않는다. 그 밖의 시간 초과(서버에 묻지 않고 띄우는 빈 페이지, 쿠키 넣기 등)는 사이트가 아니라
    브라우저가 멈춘 것이라 다른 연결 실패처럼 재시도하는 `EntryConnectError` 다. 재시도는 브라우저를 새로 띄운다. 첫 화면
    시간 초과로 내면 60초 동안 기억돼(`remember_if_fatal`) 다시 띄우면 됐을 실패를 '첫 화면을 40초 안에 열지 못했다'로 잘못
    알렸다.

    본문 없는 4xx·5xx 첫 화면 문서(`HTTP_RESPONSE_CODE_NET_ERROR`)는 여기로 오지 않는다. `_launch` 가 그 문서의 상태로
    `finish_entry` 를 불러 판정한다. 그 판정이 돌아오는 경우에만(일어나지 않는다) 마지막 줄의 `EntryConnectError` 가 된다.
    """
    # 리뷰 오탐 (2026-11-24): 아래 message 에 싣는 `line` 의 주소는 사이트가 넘긴 주소가 아니라 goto 에 넘긴 `entry_url` 이라 토큰이
    # 나가지 않는다. patchright 드라이버는 이동 실패를 `${errorText} at ${url}` 로 만들고 그 url 은 Page.navigate 에 넘긴 주소다
    # (드라이버 coreBundle.js 의 `_navigate`). 사이트 주소가 실리는 'interrupted by another navigation to <주소>' 는 우리 이동이
    # 문서를 받기 전에 다른 이동이 시작돼야 하는데, 첫 화면 탭은 새로 연 빈 탭이라 그 전에 사이트 JS 가 돌 수 없다. 302·JS·meta 로
    # 다른 출처로 넘어가면 이동은 성공하고 `finish_entry` 가 출처만 낸다 (로컬 실측: 302 뒤 인증서 오류·연결 거부도 `at <entry_url>`)
    line = _first_line(error)
    if isinstance(error, _playwright_timeout_error()):
        if opening_entry:
            return EntryPageError(
                code='NETWORK_ERROR',
                message=f'브라우저로 첫 화면({entry_url})을 {ENTRY_TIMEOUT_MS / 1000:g}초 안에 열지 못했습니다: {line}',
                hint='사이트가 느리거나 네트워크·프록시가 막혔을 수 있습니다. 잠시 뒤 한 번만 다시 부르고, 같으면 다른 소스를 쓰세요',
            )
        return EntryConnectError(f'브라우저 탭을 준비하다 시간이 초과됐습니다: {line}')
    if any(code in line for code in SITE_PROXY_NET_ERRORS):
        return EntryPageError(
            code='NETWORK_ERROR',
            message=f'프록시가 첫 화면({entry_url}) 연결을 거절했습니다: {line}',
            hint=PROXY_HINT,
        )
    if any(code in line for code in PROXY_NET_ERRORS):
        return _proxy_error(line)
    if CERT_NET_ERROR in line:
        return EntryPageError(
            code='NETWORK_ERROR',
            message=f'브라우저가 첫 화면({entry_url})의 인증서를 믿지 못했습니다: {line}',
            hint=CERT_HINT,
        )
    return EntryConnectError(f'브라우저로 첫 화면을 열지 못했습니다: {line}')


def install_chromium() -> dict[str, str]:
    """baenggipyo 가 쓰는 Chromium 을 내려받고(이미 있으면 건너뛴다) 실제로 한 번 띄워 본다 (`baenggipyo install-browser`).

    baenggipyo 의 patchright 로 받으므로 patchright 버전에 맞는 Chromium 이 깔린다. baenggipyo 는 전체 Chromium
    (`channel='chromium'`)만 띄워 headless 셸은 받지 않는다 (`--no-shell`, 약 270MB 덜 받는다). 내려받기 진행은
    사람이 볼 때(stderr 가 터미널이거나 `--verbose`)만 patchright 가 stderr(fd 2)에 바로 쓴다. 에이전트처럼 stderr 를
    파이프로 읽으면 에러 JSON 만 가도록 진행 출력을 받아 두었다가, 실패하면 원인 몇 줄을 에러 JSON 의 `output` 에 싣는다.

    Raises:
        BaenggipyoError: 내려받지 못했으면 BROWSER_INSTALL_FAILED, 받았는데 띄우지 못했으면 BROWSER_UNAVAILABLE.
    """
    show_progress = sys.stderr.isatty() or logger.isEnabledFor(logging.INFO)
    done = subprocess.run(
        [sys.executable, '-m', 'patchright', 'install', '--no-shell', 'chromium'],
        stdout=2 if show_progress else subprocess.PIPE, stderr=None if show_progress else subprocess.STDOUT, check=False,
    )
    if done.returncode != 0:
        # Node 스택 줄(`at ...`)은 빼고 원인 문장만 싣는다
        output = [] if show_progress else [
            line for line in done.stdout.decode(errors='replace').splitlines()
            if line.strip() and not line.lstrip().startswith('at ')
        ][-5:]
        raise BaenggipyoError(
            code='BROWSER_INSTALL_FAILED',
            message=f'Chromium 을 내려받지 못했습니다 (patchright install 종료 코드 {done.returncode})',
            hint=(
                f"{'위에 나온 patchright 메시지' if show_progress else '이 에러의 output'}에 원인(네트워크·권한 등)이 있습니다. "
                '네트워크(회사망·프록시 차단 등)면 확인하고 다시 실행하고, 권한 오류(PermissionError 등)면 보안 프로그램이 '
                '막았거나 설치가 깨진 것이라 baenggipyo 를 다시 설치하세요'
            ),
            details={'output': output} if output else None,
        )
    # 모듈 docstring 대로 브라우저를 쓸 때만 불러온다
    from patchright.sync_api import sync_playwright

    # OSError(드라이버 node 를 실행하지 못함)는 `_launch` 와 달리 잡지 않는다. 그때는 위 `patchright install` 하위 프로세스가
    # 같은 드라이버를 먼저 실행하다 실패해 BROWSER_INSTALL_FAILED 로 끝나 여기까지 오지 않는다 (2026-11-10 실측: 드라이버
    # 실행 권한을 뺀 patchright 사본에서 install 이 PermissionError, 종료 코드 1)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel='chromium')
            version = browser.version
            browser.close()
            executable = playwright.chromium.executable_path
    except _playwright_error() as error:
        raise _launch_error(error) from error
    return {'browser': 'chromium', 'version': version, 'executable': executable}


def _has_cookie(cookies: Sequence[Mapping[str, object]], name: str) -> bool:
    return any(cookie.get('name') == name for cookie in cookies)


def _state_path(source: str) -> Path:
    return state_dir() / 'browser' / f'{source}.json'


def _load_state(source: str) -> dict[str, Any]:
    """남긴 쿠키 상태. 없거나 깨졌으면 빈 상태라 첫 화면부터 연다. 쓰기는 원자적이라 잠그지 않고 읽는다."""
    try:
        data = json.loads(_state_path(source).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class _ProfileDir:
    """`BrowserOptions.persistent_profile` 가 쓰는 브라우저 기록 폴더 하나와 그 잠금.

    Attributes:
        path: Chromium 의 `user_data_dir`.
        lock: 잠금을 쥔 파일. 기록 폴더면 `<source>-profile.lock`, 임시 폴더(`temporary`)면 폴더 안의 `TEMP_PROFILE_LOCK_NAME`,
            잠그지 못했으면 None.
        temporary: 다른 명령이 기록 폴더를 쓰는 중이라 새로 만든 임시 폴더인지. 임시 폴더는 닫을 때 지우고, 끊겨 닫지 못하고 끝나면
            프로세스가 끝날 때 지운다. 그래도 Chromium 이 닫히며 다시 쓴 파일은 다음 명령이 치운다 (`_sweep_temp_profiles`).
    """

    def __init__(self, path: Path, lock: IO[str] | None, temporary: bool) -> None:
        self.path = path
        self.lock = lock
        self.temporary = temporary
        if temporary:
            # Ctrl+C 로 끊겨 닫기(`release`)가 불리지 않고 끝나도 임시 폴더가 남지 않게 한다
            atexit.register(self._remove_at_exit)

    def _remove_at_exit(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def release(self, remove: bool) -> None:
        """폴더를 지우고(`remove` 이거나 임시 폴더) 잠금을 푼다. 지우다 실패하면 경고만 남긴다.

        Chromium 이 닫힌 뒤에 불러야 한다. 기록 폴더는 잠금을 쥔 채 지워, 지우는 사이 다른 명령이 그 폴더를 쓰지 않게 한다.
        임시 폴더는 잠금 파일이 폴더 안이라 (Windows 는 열린 파일을 지우지 못한다) 잠금을 먼저 풀고 지운다.
        """
        if self.temporary:
            self._unlock()
        try:
            if remove or self.temporary:
                try:
                    shutil.rmtree(self.path)
                except FileNotFoundError:
                    pass
                except OSError as error:
                    logger.warning('브라우저 기록 폴더를 지우지 못했습니다 (%s): %s', self.path, error)
        finally:
            if self.temporary:
                atexit.unregister(self._remove_at_exit)
            self._unlock()

    def _unlock(self) -> None:
        lock, self.lock = self.lock, None
        if lock is not None:
            with suppress(OSError):
                _unlock_nonblocking(lock)
            with suppress(OSError):
                lock.close()


def _try_lock_nonblocking(file: IO[str]) -> bool:
    """기다리지 않고 배타 잠금을 건다. 다른 쪽이 쥐고 있으면 False. 그 밖의 `OSError` 는 그대로 올린다."""
    if sys.platform == 'win32':
        file.seek(0)
        try:
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EDEADLK):
                return False
            raise
    else:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
    return True


def _unlock_nonblocking(file: IO[str]) -> None:
    if sys.platform == 'win32':
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(file, fcntl.LOCK_UN)


def _sweep_temp_profiles(source: str, now: float) -> None:
    """끊겨 남은 같은 소스의 임시 기록 폴더(`baenggipyo-<source>-profile-*`)를 치운다. 실패해도 예외를 내지 않는다.

    `STALE_TEMP_PROFILE_SECONDS` 보다 덜 지난 폴더, 다른 명령이 잠금 파일을 쥔 폴더는 건드리지 않는다.
    """
    try:
        folders = list(Path(tempfile.gettempdir()).glob(f'baenggipyo-{source}-profile-*'))
    except OSError as error:
        logger.debug('임시 기록 폴더를 찾지 못했습니다: %s', error)
        return
    for folder in folders:
        try:
            if not folder.is_dir() or now - folder.stat().st_mtime < STALE_TEMP_PROFILE_SECONDS:
                continue
            lock_path = folder / TEMP_PROFILE_LOCK_NAME
            if lock_path.exists():
                with open(lock_path, 'a+', encoding='utf-8') as lock:
                    if not _try_lock_nonblocking(lock):
                        continue
                    _unlock_nonblocking(lock)
            shutil.rmtree(folder, ignore_errors=True)
        except OSError as error:
            logger.debug('임시 기록 폴더를 치우지 못했습니다 (%s): %s', folder, error)


def _acquire_profile(source: str) -> _ProfileDir:
    """이 소스의 브라우저 기록 폴더(`<state_dir>/browser/<source>-profile`)를 쓸 권리를 얻는다.

    잠금 파일(`<source>-profile.lock`)에 기다리지 않는 배타 잠금을 건다. 얻으면 그 기록 폴더를 이어 쓰고, 다른 명령이 쥐고
    있거나 잠금 파일을 열지 못했으면(경고 로그) 새 임시 폴더를 쓴다. 같은 폴더를 Chromium 둘이 함께 쓰면 안 되기 때문이다.
    잠금은 닫을 때 푼다 (`_ProfileDir.release`). 임시 폴더는 시스템 임시 폴더에 만들고 그 안의 잠금 파일을 잠근다.
    시작할 때 끊겨 남은 같은 소스의 임시 폴더(`_sweep_temp_profiles`)를 치운다.
    """
    _sweep_temp_profiles(source, time.time())
    base = state_dir() / 'browser'
    lock: IO[str] | None = None
    try:
        base.mkdir(parents=True, exist_ok=True)
        lock = open(base / f'{source}-profile.lock', 'a+', encoding='utf-8')
        if _try_lock_nonblocking(lock):
            return _ProfileDir(base / f'{source}-profile', lock, temporary=False)
    except OSError as error:
        logger.warning('브라우저 기록 폴더를 잠그지 못해 임시 폴더를 씁니다: %s', error)
    if lock is not None:
        with suppress(OSError):
            lock.close()
    path = Path(tempfile.mkdtemp(prefix=f'baenggipyo-{source}-profile-'))
    temp_lock: IO[str] | None = None
    try:
        temp_lock = open(path / TEMP_PROFILE_LOCK_NAME, 'a+', encoding='utf-8')
        if not _try_lock_nonblocking(temp_lock):
            temp_lock.close()
            temp_lock = None
    except OSError as error:
        logger.warning('임시 기록 폴더의 잠금 파일을 잠그지 못했습니다: %s', error)
        if temp_lock is not None:
            with suppress(OSError):
                temp_lock.close()
        temp_lock = None
    return _ProfileDir(path, temp_lock, temporary=True)


def usable_cookies(
    state: Mapping[str, Any], entry_url: str, ready_cookie: str | None, now: float
) -> list[dict[str, Any]] | None:
    """남긴 쿠키로 바로 요청해도 되면 아직 살아 있는 쿠키, 첫 화면을 다시 열어야 하면 None.

    첫 화면을 연 지 `REFRESH_SECONDS` 가 지났거나, 시각이 앞뒤가 안 맞거나, 남긴 페이지 주소가 지금 첫 화면(`entry_url`)과
    출처가 다르거나, `ready_cookie` 가 없거나 만료됐으면 다시 연다. 출처를 보는 것은 baenggipyo 를 업데이트해 첫 화면 출처가
    바뀌었을 때 옛 주소로 띄우면 `finish_entry` 가 다른 출처로 보고 명령을 실패시키기 때문이다.

    `ready_cookie` 가 없는 상태는 첫 화면이 `READY_COOKIE_TIMEOUT_SECONDS` 안에 그 쿠키를 심지 못한 것이다. 이어 쓰지 않고
    다음 명령이 첫 화면을 다시 열어 쿠키를 받게 한다. 일부러 그렇다: 그 쿠키는 사이트가 사람 브라우저마다 심는 식별 쿠키라
    (네이버 항공의 `NNB`) 없는 채로 30분을 이어 보내는 것보다 다시 여는 쪽이 사람 방문과 같다. 늦게 심는 환경이면 명령마다 첫 화면
    요청이 더 나가지만 `RateLimiter.record` 로 세어 요청 제한은 지킨다. `NNB` 는 첫 화면을 연 지 1초 안에 생긴다 (2026-11-24 실측).
    """
    opened_at, page_url, cookies = state.get('opened_at'), state.get('page_url'), state.get('cookies')
    # 빼지 않고 비교만 한다. 상태 파일에 실수로 바꿀 수 없을 만큼 큰 정수가 있으면 `now - opened_at` 이 OverflowError 를
    # 내 그 소스의 모든 명령이 INTERNAL_ERROR 로 실패했다. 비교는 크기와 상관없이 터지지 않는다
    if not isinstance(opened_at, (int, float)) or not now - REFRESH_SECONDS < opened_at <= now:
        return None
    if not isinstance(page_url, str) or not isinstance(cookies, list) or _origin(page_url) != _origin(entry_url):
        return None
    alive = [
        cookie for cookie in cookies
        if isinstance(cookie, dict) and isinstance(cookie.get('name'), str)
        and not (isinstance(cookie.get('expires'), (int, float)) and 0 < cookie['expires'] <= now)
    ]
    if ready_cookie is not None and not _has_cookie(alive, ready_cookie):
        return None
    return alive


def returning_cookies(state: Mapping[str, Any], entry_url: str, now: float) -> list[dict[str, Any]]:
    """첫 화면을 다시 열 때 먼저 넣을 쿠키. 남긴 쿠키 중 만료 시각이 있고 아직 살아 있는 것(방문자 식별 쿠키 등)이다.

    브라우저를 닫았다 다시 들어온 사람과 같게, 닫으면 사라지는 세션 쿠키(만료 시각 없음)는 넣지 않는다. 아무것도 넣지
    않으면 30분마다 사이트가 방문자 쿠키를 새로 심는다. 403 으로 버린 쿠키는 파일이 없어 넣지 않고, 남긴 페이지 출처가 지금 첫 화면과 다르면(`usable_cookies` 와
    같은 이유) 넣지 않는다.
    """
    page_url, cookies = state.get('page_url'), state.get('cookies')
    if not isinstance(page_url, str) or not isinstance(cookies, list) or _origin(page_url) != _origin(entry_url):
        return []
    return [
        cookie for cookie in cookies
        if isinstance(cookie, dict) and isinstance(cookie.get('name'), str)
        and isinstance(cookie.get('expires'), (int, float)) and cookie['expires'] > now
    ]


def _cookie_keys(cookies: Sequence[Mapping[str, object]]) -> frozenset[tuple[object, ...]]:
    return frozenset(
        (cookie.get('name'), cookie.get('domain'), cookie.get('path'), cookie.get('value')) for cookie in cookies
    )


class _EntryCookies:
    """새로 연 첫 화면이 쿠키를 다 심었는지 본다 (`_launch` 가 load 뒤 `READY_COOKIE_POLL_MS` 마다 부른다).

    `ready_cookie` 가 있고, 이번 방문에서 사이트가 쿠키를 하나라도 심거나 바꿨고(넣어 둔 `returning` 뿐이면 사이트 JS 가 아직
    돌지 않은 것이다. ready_cookie 를 넣어 두고 다시 열면 처음부터 있다), 그 뒤 `READY_COOKIE_SETTLE_SECONDS` 동안 더
    바뀌지 않았으면 다 심은 것이다. `ready_cookie` 가 없는 소스는 기다리지 않는다.
    """

    def __init__(self, ready_cookie: str | None, returning: Sequence[Mapping[str, object]]) -> None:
        self.ready_cookie = ready_cookie
        self.returning = _cookie_keys(returning)
        self.last: frozenset[tuple[object, ...]] | None = None
        self.changed_at = 0.0

    def ready(self, cookies: Sequence[Mapping[str, object]], now: float) -> bool:
        if self.ready_cookie is None:
            return True
        keys = _cookie_keys(cookies)
        if keys != self.last:
            self.last, self.changed_at = keys, now
        return (
            _has_cookie(cookies, self.ready_cookie) and bool(keys - self.returning)
            and now - self.changed_at >= READY_COOKIE_SETTLE_SECONDS
        )


def _proxy_settings(proxy: str | None, target_url: str) -> dict[str, str] | Literal['direct'] | None:
    """브라우저에 넘길 Playwright 프록시 설정. 'direct' 면 프록시 없이 바로 연결하고, None 이면 Chromium 이 OS 프록시 설정을 쓴다.

    `proxy` 를 주지 않았으면 환경 변수(`HTTPS_PROXY`·`ALL_PROXY`·`NO_PROXY`, 소문자도)를 읽는다. httpx 는 transport 를
    넘기면 환경 변수 프록시를 쓰지 않고, Chromium 은 GNOME 리눅스·Windows·macOS 에서 환경 변수 대신 OS 설정을 봐서
    예전(httpx)처럼 환경 변수에 기대던 사용자가 프록시를 잃는다 (2026-11-09 실측: GNOME 세션에서 HTTP_PROXY 무시).
    `NO_PROXY=*` 는 httpx 처럼 어떤 프록시도 쓰지 않는다는 뜻이라, None 으로 두면 Chromium 이 OS 프록시로 보내 'direct' 다.
    scrapling 의 문자열 `proxy` 는 비밀번호의 `%XX` 를 풀지 않고 스킴 없는 주소(`127.0.0.1:8080`)를 받지 않아 직접 나눈다.

    Chromium 은 http·https·socks4·socks5 프록시만 받는다. curl·httpx 의 `socks5h`(프록시가 이름을 푼다)·`socks4a` 는 Chromium 의
    socks5·socks4 가 이미 그렇게 해 바꿔 넘긴다. 그대로 넘기면 `ERR_NO_SUPPORTED_PROXIES` 였다 (2026-11-09 실측).

    Raises:
        ProxyError: 프록시 주소를 해석할 수 없거나(포트가 숫자가 아님 등), Chromium 이 받지 않는 스킴이거나, SOCKS 프록시에
            아이디·비밀번호가 있을 때 (Chromium 이 지원하지 않아 띄우다 실패하고 BROWSER_UNAVAILABLE 이 됐다, 같은 날 실측).
            메시지에 비밀번호는 넣지 않는다.
    """
    bypass: list[str] = []
    # 빈 문자열(`proxy=''`)은 '프록시 없음'이 아니라 잘못된 주소로 보고 아래에서 ProxyError 를 낸다. httpx
    # 도 `proxy=''` 를 `ValueError: Unknown scheme for proxy URL` 로 거절한다. 환경
    # 변수를 따르려면 None, 프록시 없이 바로 연결하려면 `NO_PROXY=*` 다
    if proxy is None:
        proxies = urllib.request.getproxies_environment()
        proxy = proxies.get(urlsplit(target_url).scheme) or proxies.get('all')
        no_proxy = [entry.strip() for entry in proxies.get('no', '').split(',') if entry.strip()]
        if '*' in no_proxy:
            return 'direct'
        if not proxy:
            return None
        for entry in no_proxy:
            entry = entry.split('://', 1)[-1]
            # 스킴만 적은 항목(`http://`)은 뺄 호스트가 없어 건너뛴다 (빈 문자열이면 아래 entry[0] 이 IndexError 였다)
            if not entry:
                continue
            bypass.append(entry)
            # curl·httpx 처럼 example.com 이 하위 도메인까지 덮게 한다 (Chromium 은 그 호스트만 뺀다)
            if entry[0].isalpha() and entry != 'localhost' and not entry.startswith('.'):
                bypass.append(f'*.{entry}')
    parts = urlsplit(proxy if '://' in proxy else f'http://{proxy}')
    # 아이디·비밀번호를 뺀 주소. 에러 메시지에 쓴다
    shown = f'{parts.scheme}://{parts.netloc.rpartition("@")[2]}'
    scheme = PROXY_SCHEME_ALIASES.get(parts.scheme.lower(), parts.scheme.lower())
    hostname = parts.hostname
    try:
        port = parts.port
    except ValueError as error:
        raise ProxyError(f'프록시 주소를 해석하지 못했습니다 ({shown}): {error}') from None
    if scheme not in BROWSER_PROXY_SCHEMES or not hostname:
        raise ProxyError(
            f'브라우저가 쓸 수 없는 프록시 주소입니다 ({shown}). http·https·socks4·socks5 프록시만 쓸 수 있습니다'
        )
    if scheme.startswith('socks') and (parts.username or parts.password):
        raise ProxyError(f'브라우저(Chromium)는 아이디·비밀번호가 있는 SOCKS 프록시를 쓰지 못합니다 ({shown})')
    host = f'[{hostname}]' if ':' in hostname else hostname
    settings = {'server': f'{scheme}://{host}' + (f':{port}' if port else '')}
    if parts.username:
        settings['username'] = unquote(parts.username)
    if parts.password:
        settings['password'] = unquote(parts.password)
    if bypass:
        settings['bypass'] = ','.join(bypass)
    return settings


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme, parts.netloc


def _count_preflights(session: 'CDPSession', state: '_BrowserState') -> None:
    """CDP 세션의 `Network.requestWillBeSent` 중 CORS 사전 요청(OPTIONS)을 요청 제한에 센다 (`_BrowserState.count_preflight`).

    사전 요청 표식은 `_BlockedResponses._on_request` 와 같다. `Network.enable` 은 부르는 쪽이 보낸다.
    """

    def on_request(event: dict[str, Any]) -> None:
        initiator = event.get('initiator') or {}
        if initiator.get('type') == 'preflight':
            state.count_preflight(event['request']['url'])

    session.on('Network.requestWillBeSent', on_request)


def _watch_entry(context: 'BrowserContext', page: 'Page', state: '_BrowserState') -> 'CDPSession | None':
    """첫 화면을 여는 동안 정적 파일을 CDP Fetch 로 막고(`BLOCKED_FETCH_PATTERNS`, `block_entry_resources`), 첫 화면 JS 가 보낸
    CORS 사전 요청을 요청 제한에 센다 (`_BrowserState.count_preflight`, `count_entry_requests`). 첫 화면을 다 연 뒤 `detach` 한다.

    둘 다 꺼져 있으면 CDP 세션을 붙이지 않고 None 을 돌려준다. 하나만 켜졌으면 그 부분만 한다.
    """
    options = state.options
    if not (options.block_entry_resources or options.count_entry_requests):
        return None
    session = context.new_cdp_session(page)

    def fail(event: dict[str, Any]) -> None:
        # 떼는(`detach`) 사이에 온 요청은 답할 세션이 없다. 그 에러를 두면 patchright 가 traceback 을 stderr 로 내보냈다
        # (2026-11-10 로컬 실측). 떼면 Chromium 이 멈춘 요청을 풀어 주고, 첫 화면은 곧 빈 페이지로 바뀐다
        with suppress(_playwright_error()):
            session.send('Fetch.failRequest', {'requestId': event['requestId'], 'errorReason': 'BlockedByClient'})

    if options.block_entry_resources:
        session.on('Fetch.requestPaused', fail)
    if options.count_entry_requests:
        _count_preflights(session, state)
    if options.block_entry_resources:
        session.send('Fetch.enable', {'patterns': list(BLOCKED_FETCH_PATTERNS)})
    if options.count_entry_requests:
        session.send('Network.enable')
    return session


# route 가 하나라도 걸린 탭에서는 patchright 가 CORS 사전 요청(OPTIONS)을 서버에 보내지 않고 브라우저 안에서 허용(204)으로
# 답하고 HTTP 캐시도 끈다 (patchright coreBundle.js `needsRequestInterception`). 실제 Chrome 과 달리 다른 출처 API 에
# OPTIONS 없이 본 요청만 가므로, route 는 빈 탭 띄우기(`_fulfill_blank`)에만 잠깐 쓰고 모두 걷는다. 첫 화면의 정적 파일은
# route 가 아니라 CDP Fetch 로 막는다 (`BLOCKED_FETCH_PATTERNS`)
# (2026-11-09 로컬 실측: route 가 있으면 사전 요청을 403 으로 거절하는 서버에도 본 요청이 200, 걷으면 OPTIONS 가 간다)
UNROUTE_BEHAVIOR: Literal['ignoreErrors'] = 'ignoreErrors'


def _browser_owned(name: str) -> bool:
    """fetch 에 넘기지 않고 브라우저 값을 그대로 쓸 헤더인지 (`BROWSER_OWNED_HEADERS`·`BROWSER_OWNED_HEADER_PREFIXES`)."""
    lowered = name.lower()
    return lowered in BROWSER_OWNED_HEADERS or lowered.startswith(BROWSER_OWNED_HEADER_PREFIXES)


def _fulfill_blank(route: 'Route') -> None:
    """탭을 이 주소로 띄울 때만 서버에 묻지 않고 브라우저 안에서 빈 페이지를 돌려준다 (그 출처의 탭만 필요할 때).

    같은 주소로 보내는 우리 fetch 는 다음 route 로 넘겨 서버에 보낸다. 넘기지 않으면 남긴 쿠키로 띄운 페이지와
    같은 주소의 HTML 요청까지 빈 페이지가 되어 PARSE_ERROR 가 난다.

    빈 페이지에도 아이콘 링크(`data:`)를 둔다. 없으면 Chromium 이 스스로 그 사이트의 `/favicon.ico` 를 쿠키를 실어 요청했고,
    이 요청은 route·`request` 이벤트·CDP Fetch 어디에도 잡히지 않아 요청 제한에도 세지 않았다 (2026-11-10 로컬 재현).
    """
    if route.request.is_navigation_request():
        route.fulfill(status=200, content_type='text/html', body=BLANK_PAGE_HTML)
    else:
        route.fallback()


def _fetch_arguments(
    request: Request, default_timeout: float, credentials: Literal['same-origin', 'include'],
) -> tuple[dict[str, Any], float]:
    """`FETCH_SCRIPT` 에 넘길 인자와 timeout(초). 브라우저가 정하는 헤더는 뺀다.

    `credentials` 는 fetch 의 쿠키 정책이다 (`BrowserOptions.fetch_credentials`). 요청에 `STREAM_SECONDS_EXTENSION` 이 있으면
    그 초를 `streamMs` 로 넘겨 스크립트가 본문을 줄 단위로 읽고 마지막 `data:` 줄만 돌려주게 한다. 없으면 `streamMs` 는 None 이다.

    헤더는 provider 가 붙인 것(사이트 웹 코드가 붙이는 헤더를 실측한 것)을 그대로 넘긴다.

    본문은 sync `read()` 로 읽는다. 비동기 transport 는 부르기 전에 `aread()` 로 읽어 둔다 (이미 읽은 본문은 그대로 준다).

    timeout 은 httpx 의 read 값을 응답 헤더가 올 때까지(CORS 사전 요청·연결 포함)와 본문 조각 사이 간격의 제한으로 쓴다. httpx 의
    읽기 제한처럼 조금씩 계속 오는 응답은 전체가 timeout 보다 길어도 받는다. fetch 하나 전체의 제한으로 두면 조금씩 계속 오는
    22초짜리 응답이 20초에 `ReadTimeout` 이 된다. client 의 timeout 을 httpx 의 timeout 과 같은 뜻으로 두려고 조각 사이로 잰다.
    요청 하나 전체의 상한은 따로 두지 않는다. 조각이 timeout 안에 끝없이 이어지는 응답은 브라우저 스레드를 Ctrl+C 까지 붙잡지만,
    예전 httpx 도 읽기 제한만 있어 같았다. 상한을 두면 timeout 에
    두 번째 뜻이 생긴다. 스트림 모드(`STREAM_SECONDS_EXTENSION`)만 마감이 있다.

    요청에 timeout 이 없으면 클라이언트의 timeout(`default_timeout`)을 쓴다. httpx 는 인증 흐름(httpx `Auth`)이 만든 요청에
    timeout 을 붙이지 않아, 고정값을 쓰면 `timeout=60` 클라이언트에서도 그 요청만 20초에 끊겼다 (2026-11-10 로컬 실측. 예전 httpx 는 이 요청을 시간 제한 없이 기다렸다).
    read 가 None(httpx `Timeout(None)`, 무제한)이어도 무제한으로 두지 않고 클라이언트 timeout 을 쓴다. 브라우저 fetch 를
    끝없이 기다리면 그 transport 의 브라우저 스레드가 묶여 Ctrl+C 로만 풀린다. CLI 는 늘 숫자 timeout 이라(`BaseHTTPClient
    .timeout` 은 0보다 큰 수) 라이브러리에서 무제한을 준 경우만 다르다.
    """
    headers = {name: value for name, value in request.headers.items() if not _browser_owned(name)}
    content = request.read()
    stream_seconds = request.extensions.get(STREAM_SECONDS_EXTENSION)
    timeout = min((request.extensions.get('timeout') or {}).get('read') or default_timeout, MAX_TIMER_MS / 1000)
    return {
        'url': str(request.url),
        'method': request.method,
        'headers': headers,
        'body': base64.b64encode(content).decode() if content else None,
        'credentials': credentials,
        'timeoutMs': timeout * 1000,
        'streamMs': None if stream_seconds is None else float(stream_seconds) * 1000,
        'inlineBytes': BODY_CHUNK_BYTES,
    }, timeout


class _FailedFetch(NamedTuple):
    """fetch 가 `TypeError` 로만 끝난 요청을 CDP 이벤트로 본 것 (`_BlockedResponses.pop`).

    Attributes:
        error_text: Chromium 네트워크 오류 (`net::ERR_PROXY_CONNECTION_FAILED` 등). fetch 의 `TypeError` 에는 없다.
        cors_error: CORS 로 막혔으면 그 이유 (`PreflightMissingAllowOriginHeader` 등), 아니면 None.
        response: 받은 첫 응답의 (상태, 헤더). 본 요청이 응답을 못 받고 CORS 사전 요청(OPTIONS)만 받았으면 그 응답.
        preflight: `response` 가 사전 요청의 응답인지.
    """

    error_text: str
    cors_error: str | None
    response: tuple[int, dict[str, str]] | None
    preflight: bool


class _BlockedResponses:
    """한 탭에서 fetch 가 `TypeError` 로만 끝난 요청의 Chromium 오류와 받은 응답의 상태·헤더를 CDP 이벤트로 받아 둔다.

    fetch 는 실패 이유를 `TypeError: Failed to fetch` 하나로만 알려, 프록시에 닿지 못한 것과 CORS 에 막힌 것을 가를 수 없다.
    `Access-Control-Allow-Origin` 이 없는 다른 출처 응답(WAF 가 주는 429·5xx, 로그인 화면으로 넘기는 3xx)은 Playwright
    `response` 이벤트도 오지 않는다. CDP `Network.responseReceivedExtraInfo` 에는 받은 상태와 헤더(`Retry-After` 포함)가
    그대로 오고, `Network.loadingFailed` 에는 Chromium 오류(`errorText`)와 CORS 로 막혔는지(`corsErrorStatus`)가 온다
    (2026-11-09 로컬 실측). 그래서 막힌 429 도 요청 제한 훅이 쿨다운을 걸고, 5xx 는 재시도하며, 프록시 오류는 `ProxyError` 로 낸다.
    본문을 받다 끊긴 응답은 CORS 표시가 없어 그대로 전송 오류다.
    넘겨진(redirect) 요청은 같은 요청 ID 로 다시 오므로 요청 ID 마다 처음 주소와 첫 응답(우리가 보낸 주소의 응답)을 남긴다.
    CORS 사전 요청(OPTIONS)은 다른 요청 ID 로 오고 `initiator` 에 본 요청 ID 가 있다. 사전 요청이 거절되면 본 요청은 응답
    없이 `corsErrorStatus` 만 받으므로(같은 날 실측: 커스텀 헤더를 허용하지 않는 서버는 사전 요청을 403 으로 거절), 사전 요청의
    응답을 본 요청에 이어 둔다.

    우리 요청은 주소로 찾으므로 탭마다 한 번에 요청 하나만 보낸다 (브라우저 스레드가 차례로 보낸다, `_BrowserThread`).
    같은 주소(GraphQL 은 요청마다 같은 주소다)로 동시에 보내면 나중 요청이 앞 요청의 ID 를 덮어, 막힌 429 를 한쪽은 받고
    한쪽은 ConnectError 로 받았다 (2026-11-09 로컬 실측: 비동기 동시 POST 두 건을 3번 보내 3번 모두).
    """

    def __init__(self) -> None:
        self._id_by_url: dict[str, str] = {}
        self._url_by_id: dict[str, str] = {}
        # 사전 요청 ID → 본 요청 ID
        self._preflight_for: dict[str, str] = {}
        self._first_response: dict[str, tuple[int, dict[str, str]]] = {}
        # 요청 ID → (Chromium 오류, CORS 로 막힌 이유)
        self._failed: dict[str, tuple[str, str | None]] = {}

    def listen(self, session: Any) -> None:
        """CDP 세션에 이벤트를 단다. 부르는 쪽이 이어서 `Network.enable` 을 보낸다."""
        session.on('Network.requestWillBeSent', self._on_request)
        session.on('Network.responseReceivedExtraInfo', self._on_response)
        session.on('Network.loadingFailed', self._on_failed)

    def _on_request(self, event: Mapping[str, Any]) -> None:
        request_id = event['requestId']
        initiator = event.get('initiator') or {}
        if initiator.get('type') == 'preflight' and initiator.get('requestId'):
            self._preflight_for[request_id] = initiator['requestId']
        if event.get('type') == 'Fetch' and request_id not in self._url_by_id:
            self._url_by_id[request_id] = event['request']['url']
            self._id_by_url[event['request']['url']] = request_id

    def _on_response(self, event: Mapping[str, Any]) -> None:
        # 요청 이벤트보다 먼저 올 수도 있어 요청 ID 로만 남긴다 (CDP 는 두 이벤트의 순서를 정하지 않는다)
        self._first_response.setdefault(event['requestId'], (event['statusCode'], dict(event.get('headers') or {})))

    def _on_failed(self, event: Mapping[str, Any]) -> None:
        cors = event.get('corsErrorStatus')
        self._failed[event['requestId']] = (
            event.get('errorText') or '', (cors.get('corsError') or 'unknown') if cors else None,
        )

    def _preflight_response(self, request_id: str) -> tuple[int, dict[str, str]] | None:
        for preflight_id, main_id in self._preflight_for.items():
            if main_id == request_id and preflight_id in self._first_response:
                return self._first_response[preflight_id]
        return None

    def settled(self, request_url: str) -> bool:
        """그 주소로 보낸 마지막 요청의 실패 이벤트가 왔고, CORS 로 막혔으면 받은 응답(상태·헤더)도 왔는지.

        CDP 는 `responseReceivedExtraInfo` 와 `loadingFailed` 의 순서를 정하지 않는다 (`_on_response`). 실패만 보고 멈추면
        막힌 429·5xx 의 상태를 놓쳐 `CorsBlockedError` 가 되고 쿨다운·재시도를 건너뛴다. 그래서 CORS
        실패면 응답까지 기다린다. 사전 요청(OPTIONS)이 거절된 것(`corsError` 에 'Preflight' 가 든다)이면 본 요청은 나가지
        않아 사전 요청의 응답을 기다린다. 응답이 끝내 없는 CORS 실패는 부르는 쪽의 시간(`BLOCKED_RESPONSE_WAIT_SECONDS`)만큼
        더 기다리는데, 실패한 요청에서만이다.
        """
        request_id = self._id_by_url.get(request_url)
        failed = self._failed.get(request_id) if request_id is not None else None
        if request_id is None or failed is None:
            return False
        cors_error = failed[1]
        if cors_error is None:
            return True
        if 'Preflight' in cors_error:
            return self._preflight_response(request_id) is not None
        return request_id in self._first_response

    def pop(self, request_url: str) -> _FailedFetch | None:
        """그 주소로 보낸 마지막 요청의 실패 기록. 실패 이벤트가 오지 않았으면 None.

        남긴 기록은 모두 지운다. 탭마다 한 번에 요청 하나만 보내므로 남은 기록은 모두 이 요청이나 지난 요청 것이다. CORS 사전
        요청(OPTIONS)과 늦게 온 이벤트도 요청 ID 로 남아, 이 요청 것만 지우면 요청마다 쌓였다 (2026-11-09 로컬 실측: 요청
        2건 뒤 `_first_response` 3개).
        """
        request_id = self._id_by_url.get(request_url)
        failed = self._failed.get(request_id) if request_id is not None else None
        result = None
        if request_id is not None and failed is not None:
            response = self._first_response.get(request_id)
            preflight = False
            if response is None:
                response = self._preflight_response(request_id)
                preflight = response is not None
            result = _FailedFetch(*failed, response=response, preflight=preflight)
        self._id_by_url.clear()
        self._url_by_id.clear()
        self._preflight_for.clear()
        self._first_response.clear()
        self._failed.clear()
        return result


def _response_headers(headers: Iterable[tuple[str, str]]) -> list[tuple[bytes, bytes]]:
    """브라우저가 준 응답 헤더를 httpx 응답에 넣을 바이트 쌍으로 바꾼다. 브라우저가 이미 푼 본문의 헤더는 뺀다.

    fetch 는 헤더 값을 바이트 하나당 글자 하나(latin-1)인 문자열로 준다. 문자열 그대로 넣으면 httpx 가 ASCII 로 바꾸다
    `UnicodeEncodeError`(ValueError) 를 내, 값에 한글·é 같은 글자가 있는 헤더 하나로 정상 응답이 PARSE_ERROR 가 됐다 (예전
    httpx 전송은 받았다, 2026-11-10 로컬 재현). latin-1 로 되돌리면 서버가 보낸 바이트 그대로다. Playwright·CDP 가 이미
    UTF-8 로 풀어 준 값처럼 latin-1 로 안 되는 글자가 있으면 UTF-8 로 넣는다. httpx 는 헤더 바이트를 ASCII·UTF-8·latin-1
    순으로 읽는다.
    """
    def raw(text: str) -> bytes:
        try:
            return text.encode('latin-1')
        except UnicodeEncodeError:
            return text.encode('utf-8')

    return [(raw(name), raw(value)) for name, value in headers if name.lower() not in DECODED_RESPONSE_HEADERS]


def _fetched_headers(result: dict[str, Any], page_headers: Mapping[str, Mapping[str, str]]) -> list[tuple[str, str]]:
    """fetch 가 받은 응답 헤더에, CORS 가 페이지 JS 에 감춘 헤더를 브라우저 `response` 이벤트의 헤더(`page_headers`)로 채운다."""
    headers = [(name, value) for name, value in result['headers']]
    shown = {name for name, _ in headers}
    return headers + [(name, value) for name, value in page_headers.get(result['url'], {}).items() if name not in shown]


def _to_response(
    request: Request,
    result: dict[str, Any],
    timeout: float,
    page_headers: Mapping[str, Mapping[str, str]],
    failed: _FailedFetch | None
) -> Response:
    """`FETCH_SCRIPT` 결과를 httpx 응답으로 바꾼다. fetch 실패는 재시도 술어가 아는 httpx 예외로 낸다.

    다른 출처 응답은 CORS 가 허용한 헤더만 페이지 JS 에 보여 `Retry-After` 등이 빠진다 (2026-11-09 로컬 실측:
    `Access-Control-Expose-Headers` 가 없는 서버). 브라우저 `response` 이벤트가 받은 전체 헤더(`page_headers`,
    최종 URL 별)에서 빠진 것을 채운다.

    fetch 가 실패하면 `failed`(`_BlockedResponses.pop`)로 이유를 가른다.

    - CORS 에 막혔는데 응답을 받았으면 그 상태·헤더로 본문 없는 응답을 만든다. 그러면 429 는 요청 제한 훅이, 3xx·4xx·5xx 는
      `raise_for_status` 와 재시도 술어가 예전(httpx)처럼 다룬다. 2xx 는 본문이 있어야 하는 응답이라 빈 본문으로 넘기지 않고
      `CorsBlockedError` 로 낸다 (빈 본문 200 이 성공으로 넘어갔다, 2026-11-09 로컬 실측).
    - CORS 사전 요청(OPTIONS)이 거절돼 본 요청이 나가지 못했으면, 사전 요청이 받은 429·5xx 만 응답으로 만든다 (예전 httpx 는
      사전 요청 없이 본 요청이 그 응답을 받았다). 그 밖의 사전 요청 실패는 사이트가 이 요청 헤더를 허용하지 않는 것이라
      `CorsBlockedError` 다. 사전 요청의 403·404 를 응답으로 넘기면 404·403 을 '없는 ID'로 읽는 호출부가
      차단·거절을 없는 ID 로 알릴 수 있다.
    - 프록시 오류(`PROXY_NET_ERRORS`)는 재시도하지 않는 `ProxyError`, 인증서
      오류(`CERT_NET_ERROR`)는 재시도하지 않는 `CertificateError`, 그 밖의 연결 실패는 Chromium 오류를 메시지에 붙인
      `ConnectError` 다. 첫 화면을 남긴 쿠키로 띄우면 서버에 묻지 않아, 프록시·인증서 오류는 우리 요청에서 처음 드러난다.
    """
    if result.get('error') == 'BodyTimeout':
        # 본문이 멈췄어도 상태·헤더는 받았다. 3xx 이상은 CORS 에 막힌 응답처럼 본문 없이 넘겨 429 는 요청 제한 훅이 Retry-After 로
        # 쿨다운을 걸고, 그 밖은 `raise_for_status`·재시도 술어가 다룬다 (예전 httpx 도 응답 훅이 본문을 읽기 전에 돌았다).
        # 2xx 는 본문이 있어야 하는 응답이라 `ReadTimeout` 이다
        if result['status'] >= 300:
            return Response(
                status_code=result['status'],
                headers=_response_headers(_fetched_headers(result, page_headers)),
                content=b'',
                request=request,
            )
        raise ReadTimeout(f'브라우저 요청의 응답 본문이 {timeout:.10g}초 동안 오지 않았습니다', request=request)
    if 'error' in result:
        if result['error'] == 'AbortError':
            raise ReadTimeout(f'브라우저 요청의 응답이 {timeout:.10g}초 안에 오지 않았습니다', request=request)
        if failed is not None and failed.cors_error is not None:
            status = failed.response[0] if failed.response is not None else None
            if failed.response is not None and status is not None and (
                status >= 300 and not failed.preflight or status == 429 or status >= 500
            ):
                return Response(
                    status_code=status,
                    headers=_response_headers(failed.response[1].items()),
                    content=b'',
                    request=request,
                )
            received = '' if status is None else f'{"CORS 사전 요청 " if failed.preflight else ""}HTTP {status} 응답, '
            raise CorsBlockedError(
                f'브라우저가 CORS 로 응답을 막았습니다 ({request.url.host}, {received}{failed.cors_error})',
                request=request,
            )
        if failed is not None and failed.error_text.startswith(PROXY_NET_ERRORS):
            raise _proxy_error(failed.error_text, request)
        if failed is not None and failed.error_text.startswith(CERT_NET_ERROR):
            raise CertificateError(f'브라우저가 {request.url.host} 의 인증서를 믿지 못했습니다: {failed.error_text}', request=request)
        reason = f' ({failed.error_text})' if failed is not None and failed.error_text else ''
        raise ConnectError(f'브라우저 fetch 실패: {result["error"]}: {result["message"]}{reason}', request=request)
    return Response(
        status_code=result['status'],
        headers=_response_headers(_fetched_headers(result, page_headers)),
        # 큰 본문은 `_handle_request` 가 조각으로 읽어 `content` 에 넣었다
        content=result['content'] if 'content' in result else base64.b64decode(result['body']),
        request=request,
    )


@dataclass(frozen=True, kw_only=True)
class BrowserOptions:
    """`BrowserTransport` 의 설정. 모두 키워드로만 받는다.

    transport 와 `_BrowserState` 가 이 객체 하나를 받는다. 같은 타입의 인자(`ready_cookie`·`proxy` 는 둘 다
    `str | None`)를 위치로 넘기면 순서가 어긋나도 타입 검사가 잡지 못해 키워드로만 받는다.

    Args:
        source: 소스 이름. 쿠키 파일 이름이 된다.
        entry_url: 처음 열 사이트 화면 (`https://flight.naver.com/`). 요청 주소와 출처가 같아야 CORS 에 걸리지 않는다.
        ready_cookie: 첫 화면을 연 뒤 생길 때까지 기다리고, 남긴 쿠키를 쓸 수 있는지 볼 쿠키 이름.
            None 이면 load 까지만 기다린다. Defaults to None.
        api_hosts: 사이트 API 호스트. 첫 화면이 이 호스트로 보낸 요청을 요청 제한에 센다. 비우면 만들 때 `entry_url` 의
            호스트로 채운다. Defaults to frozenset().
        discard_statuses: 우리 요청이 받으면 남긴 쿠키를 버릴 HTTP 상태. Defaults to `DISCARD_STATE_STATUSES`.
        rate_limiter: 소스의 요청 제한기. Defaults to None.
        proxy: 브라우저가 쓸 프록시 URL. Defaults to None.
        verify: TLS 인증서 검증. False 면 브라우저가 인증서 오류를 무시한다. Defaults to True.
        timeout: 요청에 httpx timeout 이 없을 때 쓰는 값(초). 클라이언트의 timeout 을 넘긴다 (`_fetch_arguments`).
            Defaults to `DEFAULT_TIMEOUT_SECONDS`.
        fetch_credentials: fetch 의 `credentials`. 'same-origin' 이면 첫 화면과 같은 출처에만 쿠키를 싣고, 'include' 면 다른 출처
            (API 호스트가 첫 화면과 다른 소스)에도 싣는다. Defaults to 'same-origin'.
        keep_cookies: 쿠키를 `<state_dir>/browser/<source>.json` 에 남기고 다음 명령이 이어 쓴다. False 면 그 파일을 읽지도
            쓰지도 지우지도 않아 명령마다 첫 화면을 연다 (남긴 쿠키로 빈 페이지를 띄우는 길도 타지 않는다). Defaults to True.
        persistent_profile: 브라우저 기록 폴더(`<state_dir>/browser/<source>-profile`)를 통째로 이어 쓴다. 띄우기 전에
            `<source>-profile.lock` 에 기다리지 않는 잠금을 걸고, 다른 명령이 쥐고 있으면 이번 transport 는 임시 폴더를 써 닫을 때
            지운다. 폴더에 이미 있는 쿠키는 첫 화면을 열기 전에 읽어 `ready_cookie` 판정의 '이어 쓴 쿠키' 로 두므로, 사이트가 쿠키를
            새로 심거나 바꿀 때까지 `ready_timeout` 안에서 기다린다. 우리 요청이 `discard_statuses` 를 받았거나 첫 화면이
            막혀(4xx·다른 출처) 끝났으면 닫을 때 폴더를 지운다 (잠금을 얻은 폴더일 때). 보통 `keep_cookies=False` 와 함께 쓴다. Defaults to False.
        stay_on_entry_page: True 면 첫 화면을 연 뒤 빈 페이지로 바꾸지 않고 열린 첫 화면 탭에서 그대로 요청한다 (`page_url` 은
            그 탭의 주소). 남긴 쿠키로 띄우는 길(`keep_cookies`)은 열린 첫 화면이 없어 빈 페이지를 쓴다. Defaults to False.
        count_entry_requests: False 면 첫 화면이 스스로 보낸 요청과 CORS 사전 요청을 요청 제한에 세지 않고, 첫 화면의 하위 요청이
            받은 429 도 쿨다운으로 보지 않는다 (첫 화면 문서의 상태만 판정에 쓴다). Defaults to True.
        block_entry_resources: False 면 첫 화면을 여는 동안 정적 파일(`BLOCKED_RESOURCE_TYPES`)을 막지 않는다. Defaults to True.
        ready_timeout: `ready_cookie` 를 기다리는 시간 상한(초). Defaults to `READY_COOKIE_TIMEOUT_SECONDS`.
        entry_settle_seconds: 첫 화면을 새로 열었을 때만, 쿠키 준비(또는 `ready_timeout`) 뒤 이만큼(초) 더 기다리고 첫 요청을
            보낸다. 첫 화면 문서가 4xx·5xx·429 로 끝났으면 기다리지 않는다. Defaults to 0.
        entry_block_cooldown: True 면 첫 화면 문서가 `ENTRY_BLOCKED_STATUSES` 일 때 요청 제한에 차단 쿨다운을 걸고
            `RateLimitedError(reason='blocked')` 를 낸다 (요청 제한이 꺼져 있으면 `EntryPageError`). 막힌 첫 화면을
            명령마다 다시 열지 않게 한다. Defaults to False.
    """

    source: str
    entry_url: str
    ready_cookie: str | None = None
    api_hosts: frozenset[str] = frozenset()
    discard_statuses: frozenset[int] = DISCARD_STATE_STATUSES
    rate_limiter: RateLimiter | None = None
    proxy: str | None = None
    verify: bool = True
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    fetch_credentials: Literal['same-origin', 'include'] = 'same-origin'
    keep_cookies: bool = True
    persistent_profile: bool = False
    stay_on_entry_page: bool = False
    count_entry_requests: bool = True
    block_entry_resources: bool = True
    ready_timeout: float = READY_COOKIE_TIMEOUT_SECONDS
    entry_settle_seconds: float = 0.0
    entry_block_cooldown: bool = False

    def __post_init__(self) -> None:
        # 빈 api_hosts 를 여기서 채워, 쓰는 쪽이 모두 `options.api_hosts` 하나만 읽게 한다. 따로 채운 값을 두면 빈 원래 값을
        # 잘못 읽어 첫 화면 요청을 하나도 세지 않을 수 있다. frozen 이라 object.__setattr__ 로 넣는다
        if not self.api_hosts:
            object.__setattr__(self, 'api_hosts', frozenset({urlsplit(self.entry_url).netloc}))


class _BrowserState:
    """transport 의 설정(`options`)과 한 명령 동안의 기록."""

    def __init__(self, options: BrowserOptions) -> None:
        self.options = options
        self.reset()

    def session_options(self) -> dict[str, Any]:
        """scrapling `StealthySession` 에 넘길 설정."""
        # google_search 는 scrapling fetch() 의 Referer 를 구글로 바꾸는 옵션이라 끈다 (우리는 fetch() 를 쓰지 않는다).
        # scrapling 은 컨텍스트에 ignore_https_errors=True 를 기본으로 넣어 인증서를 검사하지 않는다. additional_args 가 그보다
        # 우선하므로 여기서 되돌린다. Chromium 은 인증서 파일을 받지 않고 내장 목록과 OS 에 설치된 인증서를 믿는다
        # (2026-11-09 실측: 테스트 CA 가 서명한 로컬 서버를 기본값은 통과, False 로 덮어쓰면 ERR_CERT_AUTHORITY_INVALID)
        # 프록시는 scrapling 의 `proxy` 가 아니라 Playwright 설정으로 바로 넘긴다 (`_proxy_settings`). 바로 연결은 Chromium 의
        # `--no-proxy-server` 라 OS 프록시 설정도 보지 않는다
        # 서비스 워커는 scrapling 기본값(service_workers='allow')을 그대로 둔다. 위장 기본값을 바꾸지
        # 않으려고 막지 않는다. 사이트가 서비스 워커를 쓰기 시작하면 그 워커가 처리한 요청은 page.route 가 가로채지 못해
        # (Playwright 의 알려진 제한) 서버에 묻지 않을 빈 페이지 이동이 서버로 가니, 그때 additional_args 에
        # service_workers='block' 을 넣는다
        additional_args: dict[str, Any] = {'ignore_https_errors': not self.options.verify}
        extra_flags: list[str] = []
        proxy = _proxy_settings(self.options.proxy, self.options.entry_url)
        if proxy == 'direct':
            extra_flags.append('--no-proxy-server')
        elif proxy is not None:
            additional_args['proxy'] = proxy
        return {
            'headless': True, 'locale': 'ko-KR', 'timezone_id': 'Asia/Seoul', 'google_search': False,
            'additional_args': additional_args, 'extra_flags': extra_flags,
        }

    def reset(self) -> None:
        # 브라우저를 통과한 사이트 API 요청 수 (첫 화면 + 우리 요청), 그중 첫 화면을 다 연 시점까지의 수, 우리 요청 수
        self.routed = 0
        self.routed_at_open = 0
        self.ours = 0
        self.entry_429 = False
        # 첫 화면에서 받은 429 의 `Retry-After` 중 가장 긴 것 (초). 없으면 None
        self.entry_retry_after: float | None = None
        # 명령이 끝날 때 남긴 쿠키를 버린다 (거부된 방문자 쿠키)
        self.discard = False
        # 명령이 끝날 때 브라우저 기록 폴더를 지운다 (`discard` 와 같은 판정, `BrowserOptions.persistent_profile`). 실제로 지우는 것은
        # Chromium 을 닫은 뒤 `BrowserTransport._close` 가 한다
        self.discard_profile = False
        # 명령이 끝날 때 남긴 쿠키 파일을 건드리지 않는다 (첫 화면 5xx: 사이트 장애라 방문자 쿠키를 버리지도, 이 첫 화면의
        # 쿠키로 덮지도 않는다. 파일의 첫 화면 시각이 그대로라 다음 명령이 그 쿠키를 넣고 첫 화면을 다시 연다)
        self.leave_saved = False
        self.page_url = ''
        self.opened_at = 0.0

    def count_request(self, resource_type: str, url: str) -> None:
        """첫 화면 탭의 `request` 이벤트마다 부른다. route 는 탭을 다 띄운 뒤 걷으므로 이벤트로 센다 (`UNROUTE_BEHAVIOR`)."""
        if not self.options.count_entry_requests:
            return
        if resource_type in COUNTED_RESOURCE_TYPES and urlsplit(url).netloc in self.options.api_hosts:
            self.routed += 1

    def count_preflight(self, url: str) -> None:
        """브라우저가 사이트 API 로 보낸 CORS 사전 요청(OPTIONS)을 요청 제한에 바로 남긴다 (CDP `Network.requestWillBeSent`).

        사전 요청은 요청 제한 훅도 페이지 `request` 이벤트도 거치지 않아 세지 않으면 서버로 가는 요청이 정책보다 많다. 서버가
        `Access-Control-Max-Age` 를 주지 않으면 브라우저는 5초만 기억해 주소가 다른 요청마다 OPTIONS 가 따라간다
        (2026-11-10 로컬 실측). 첫 화면과 같은 출처로 가는 요청은 사전 요청이 없다. CDP 이벤트
        처리 중이라 상태 파일을 쓰지 못해도 올리지 않는다. 올리면 patchright 가 traceback 을 stderr 로 내고, 다음 브라우저
        호출에서 그 에러를 다시 만들다 TypeError 로 바꿔 내 명령이 INTERNAL_ERROR 로 끝났다 (`record` 는
        OSError 를 `StateDirError` 로 바꿔 올리는데 OSError 만 잡고 있었다).

        사전 요청마다 상태 파일을 한 번씩 쓴다 (잠금·읽기·쓰기). 모아 두었다 한 번에 쓰지 않는 것은 비용이 작고(2026-11-10
        로컬 실측: 13번 쓰는 데 합쳐 3.5ms) 미뤄 쓰면 그사이 다른 baenggipyo 프로세스가 이 요청을
        세지 못하고, 쓰기 전에 프로세스가 끝나면 센 요청이 사라지기 때문이다.
        """
        if (
            not self.options.count_entry_requests or self.options.rate_limiter is None
            or urlsplit(url).netloc not in self.options.api_hosts
        ):
            return
        try:
            self.options.rate_limiter.record(1)
        except (StateDirError, OSError) as error:
            # 잠금 풀기·잠금 파일 닫기의 OSError(Windows `msvcrt`)는 `record` 가 StateDirError 로 감싸지 않는다
            logger.warning('CORS 사전 요청을 요청 제한에 남기지 못했습니다: %s', error)

    def on_entry_rate_limited(self, url: str, retry_after: str | None, main_document: bool) -> None:
        """첫 화면 탭이 429 응답을 받을 때마다 부른다 (`page` 의 `response` 이벤트. 본문 없는 429 첫 화면 문서도 goto 가 실패하기 전에 온다).

        사이트 API 호스트의 429 면 첫 화면에서 멈추게 표시하고(`entry_429`), 그 응답과 첫 화면 문서(`main_document`)의
        429 에서 `Retry-After` 를 남겨 `finish_entry` 가 쿨다운에 쓴다. 우리 요청의 429(`RateLimiter.on_rate_limited`)처럼
        정책 값과 서버 값 중 긴 쪽으로 쉬려는 것이다. 정책 값만 쓰면 서버가 더 쉬라고 한 사이트에 일찍 다시 보냈다
        (Retry-After 600 에 쿨다운 120초). 사이트 밖(광고·분석) 응답의 429 는 보지 않는다. 그 값으로
        쿨다운이 늘지 않게 한다.

        첫 화면 단계에서 보지 못하는 429 가 둘 있다. 다른 출처 API 가 CORS 허용(`Access-Control-Allow-Origin`) 없이 준
        429 는 브라우저가 `response` 이벤트를 주지 않고, load·쿠키 기다림 뒤에 온 응답은 리스너를 뗀 뒤라 보지 않는다. 둘 다 다음 우리 요청이 같은 429 를
        받아 `Retry-After` 대로 쉬므로(`_BlockedResponses`) 손해는 요청 1건이다 (2026-11-10 로컬 실측: CORS 없는 429 에 첫
        화면은 통과하고 다음 요청에서 쿨다운 600초). 첫 화면 탭에 CDP 기록을 미리 붙이면 잡을 수 있지만 그만큼 복잡해져
        두었다.
        """
        # 요청을 세지 않는 설정(`count_entry_requests=False`)은 하위 요청의 429 를 쿨다운으로 보지 않아 첫 화면 문서의 429 만 본다
        api = self.options.count_entry_requests and urlsplit(url).netloc in self.options.api_hosts
        if api:
            self.entry_429 = True
        if not (api or main_document) or not retry_after:
            return
        # 순환 import 를 피하려고 여기서 가져온다 (core.http 가 이 모듈을 불러온다)
        from baenggipyo.core.http import retry_after_seconds

        seconds = retry_after_seconds(retry_after)
        if seconds is not None:
            self.entry_retry_after = max(self.entry_retry_after or 0.0, seconds)

    def finish_entry(self, status: int | None, retry_after: str | None) -> None:
        """첫 화면 요청을 요청 제한에 남기고, 첫 화면이 제대로 열리지 않았으면 멈춘다.

        쿠키는 4xx·다른 출처면 버리고(거부된 방문자), 5xx 면 남긴 파일을 그대로 두고(`leave_saved`), 429 면 남긴다. 단 본문 없는
        429 첫 화면은 goto 가 실패해 쿠키를 남기지 않는다(`_launch`).

        첫 화면 요청은 미리 슬롯을 잡지 않고 연 뒤에 센다. 몇 건일지 열어 보기 전에는 모르고, 사람이 페이지를
        열 때 나가는 묶음과 같다. 그래서 첫 화면을 새로 여는 명령(소스마다 30분에 한 번, 남긴 쿠키를 버린 뒤)은 분당 상한을
        그 묶음만큼 넘을 수 있고, 이어지는 우리 요청이 그만큼 기다린다. 쿨다운은 요청 제한 훅이 슬롯을 잡을 때 보고,
        슬롯을 기다렸으면 기다린 뒤에도 다시 본다(`RateLimiter.acquire`). 기다리지 않았으면 Chromium 을 띄우는 몇 초 사이에 다른
        프로세스가 건 쿨다운은 보지 못해 첫 화면과 그 요청 한 건이 나갈 수 있고, 그다음 요청부터 막힌다. 드물고 나가는 양이 사람이
        한 번 여는 것과 같아 코드로 막지 않았다.

        Args:
            status: 새로 연 첫 화면 문서의 HTTP 상태. 남긴 쿠키로 띄웠으면 None. 본문 없는 4xx·5xx 문서는 goto 가 실패해도
                `response` 이벤트로 받은 상태다 (`_launch`).
            retry_after: 새로 연 첫 화면 문서의 Retry-After 헤더. 없거나 남긴 쿠키로 띄웠으면 None.

        Raises:
            RateLimitedError: 첫 화면 문서가 `ENTRY_BLOCKED_STATUSES` 를 받았고 `entry_block_cooldown` 과 요청 제한이 켜져 있을 때
                (reason 'blocked', 쿨다운은 정책 값). 또는 첫 화면 문서나 그 페이지의 사이트 API 요청이 429 를 받았고 요청 제한이 켜져 있을 때. 쿨다운은
                정책 값과 그 429 의 `Retry-After` 중 긴 쪽이다 (`on_entry_rate_limited`). 꺼져 있으면 멈추지 않고(문서가
                429 면 아래 HTTPStatusError), 우리 요청이 받는 429 를 보통 재시도가 다룬다 (`RateLimiter.on_rate_limited`
                와 같다). 첫 화면 호스트가 `api_hosts` 에 없어도 문서의 429 는 센다.
            EntryStatusError: 첫 화면 문서가 5xx(또는 요청 제한을 끈 429)일 때. HTTPStatusError 라 재시도한다. `handle_errors` 가
                에러 JSON 에 `entry_page: true` 를 싣는다.
            EntryPageError: 첫 화면 문서가 5xx(또는 요청 제한을 끈 429)인데 `Retry-After` 가 재시도 대기 상한(60초)보다 길
                때 (UPSTREAM_ERROR, 429 는 RATE_LIMITED. `retry_after_seconds` 를 붙인다). 첫 화면 문서가 그 밖의 4xx 일 때
                (차단 화면 등, UPSTREAM_ERROR). HTTPStatusError 로 내면 404 가
                `handle_errors` 에서 NOT_FOUND 가 돼 차단 화면을 없는 ID 로 알린다.
                첫 화면이 다른 출처(로그인·보안 확인 화면 등)로 넘어갔을 때도 (UPSTREAM_ERROR). 그 페이지에서 보내는 요청은
                출처가 달라 CORS 로 막히므로, 남겨 두면 다음 명령들도 실패한다.
                상태 코드 때문에 낸 것(다른 출처로 넘어감 제외)은 details 에 `entry_page: true` 를 싣는다. `RateLimitedError` 도
                `entry_status` 로 같은 키를 싣는다.
        """
        self.routed_at_open = self.routed
        self.record_entry_requests()
        # 429 면 쿠키를 버리지 않는다. `DISCARD_STATE_STATUSES` 에 429 를 넣지 않은 것과 같은 이유로, 쿨다운 뒤 첫 명령이 남긴
        # 쿠키로 요청 1건만 보내 풀렸는지 본다. 버리면 아직 막혀 있을지 모르는 사이트에 첫 화면 요청을 여러 건 다시 보낸다
        # (첫 화면 API 하나가 429 인 것처럼 꾸미니 쿠키가 지워졌다). `ready_cookie` 를 못 받았으면 남긴
        # 쿠키를 쓰지 않아(`usable_cookies`) 다시 연다
        if self.entry_429 or status == 429:
            if self.options.rate_limiter is not None and self.options.rate_limiter.policy.enabled:
                blocked_until = self.options.rate_limiter.block(self.entry_retry_after)
                raise RateLimitedError(
                    self.options.source,
                    blocked_until - time.time(),
                    'cooldown',
                    entry_status=429 if status == 429 else None,
                )
        if status is not None and (status >= 500 or status == 429):
            if status >= 500:
                # 사이트 장애라 방문자를 거부한 것이 아니다. 버리면 다시 넣은 방문자 쿠키(`NNB` 등)까지 지워 재시도가
                # 새 방문자로 열었다 (2026-11-10 로컬 실측: 503 한 번에 vid=old 를 지우고 재시도가 vid=new 를 받음)
                self.leave_saved = True
            # 순환 import 를 피하려고 여기서 가져온다 (core.http 가 이 모듈을 불러온다)
            from baenggipyo.core.http import (
                RETRY_MAX_WAIT_SECONDS,
                retry_after_seconds,
            )

            wait = retry_after_seconds(retry_after) if retry_after else None
            if wait is not None and wait > RETRY_MAX_WAIT_SECONDS:
                # 재시도는 상한(60초)까지만 쉬어, 더 오래 기다리라는 사이트에 첫 화면 요청(여러 건)을 그보다 일찍 다시 보냈다.
                # 다시 열지 않고 멈춘다
                seconds = int(wait + 0.999)
                raise EntryPageError(
                    code='RATE_LIMITED' if status == 429 else 'UPSTREAM_ERROR',
                    message=(
                        f'{self.options.source} 첫 화면({self.options.entry_url}) 응답이 HTTP {status} 이고 '
                        f'{seconds}초 뒤에 다시 오라고 합니다'
                    ),
                    # 5xx 는 `handle_errors` 의 첫 화면 5xx hint 처럼 사이트 장애라고 밝힌다. docs/llm.md 가 UPSTREAM_ERROR 에
                    # 업데이트를 권하되 hint 가 사이트 장애를 가리키면 권하지 않게 한다
                    hint=(
                        f'사이트 첫 화면이 서버 오류를 줬습니다. 사이트 장애일 수 있으니 {seconds}초가 지난 뒤 다시 부르고, '
                        '그사이에는 다른 소스를 쓰세요'
                        if status >= 500 else f'{seconds}초가 지난 뒤 다시 부르고, 그사이에는 다른 소스를 쓰세요'
                    ),
                    details={'status': status, 'retry_after_seconds': seconds, 'entry_page': True},
                )
            request = Request('GET', self.options.entry_url)
            # 문서의 Retry-After 를 실어 재시도(`wait_retry_after`)가 그만큼 쉬게 한다. 싣지 않으면 1초 간격으로 첫 화면을 다시 열었다
            # ASCII 가 아닌 값은 httpx 가 헤더로 받지 않아(UnicodeEncodeError) 싣지 않고 1초 쉰다 
            headers = {'Retry-After': retry_after} if retry_after and retry_after.isascii() else None
            raise EntryStatusError(
                f'첫 화면({self.options.entry_url}) 응답이 HTTP {status} 입니다',
                request=request,
                response=Response(status, headers=headers, request=request),
            )
        if status is not None and status >= 400:
            self.discard = True
            if status in ENTRY_BLOCKED_STATUSES:
                limiter = self.options.rate_limiter
                if self.options.entry_block_cooldown and limiter is not None and limiter.policy.enabled:
                    blocked_until = limiter.block()
                    raise RateLimitedError(
                        self.options.source, blocked_until - time.time(), 'blocked', entry_status=status
                    )
                raise EntryPageError(
                    code='UPSTREAM_ERROR',
                    message=f'{self.options.source} 첫 화면({self.options.entry_url}) 응답이 HTTP {status} 입니다',
                    hint='사이트가 이 접속을 막았을 수 있습니다. 업데이트로는 풀리지 않으니 다른 소스를 쓰세요',
                    details={'status': status, 'blocked': True, 'entry_page': True},
                )
            raise EntryPageError(
                code='UPSTREAM_ERROR',
                message=f'{self.options.source} 첫 화면({self.options.entry_url}) 응답이 HTTP {status} 입니다',
                hint='사이트 첫 화면 주소가 바뀌었을 수 있습니다. 다른 소스를 쓰세요',
                details={'status': status, 'entry_page': True},
            )
        if _origin(self.page_url) != _origin(self.options.entry_url):
            self.discard = True
            # 주소의 경로·쿼리·사용자 정보에 토큰이 실릴 수 있어 출처(스킴·호스트·포트)만 낸다. `netloc` 에는 `user:pass@` 가 든다
            parts = urlsplit(self.page_url)
            shown = f'{parts.scheme}://{parts.hostname}'
            if parts.port is not None:
                shown += f':{parts.port}'
            raise EntryPageError(
                code='UPSTREAM_ERROR',
                message=f'{self.options.source} 첫 화면이 다른 출처({shown})로 넘어갔습니다',
                hint='사이트가 보안 확인·로그인 화면으로 넘겼거나 첫 화면 주소가 바뀌었을 수 있습니다. 다른 소스를 쓰세요',
            )

    def keeps_entry_cookies(self, status: int | None) -> bool:
        """새로 연 첫 화면의 쿠키를 확인(`finish_entry`) 전에 남길지. 문서가 4xx·5xx(429 제외)거나 다른 출처면 남기지 않는다.

        확인 전에 남기는 것은 429 로 멈춰도 쿠키가 남게 하려는 것이다 (`_launch`). 거부된 첫 화면(4xx·다른 출처)의 쿠키까지
        먼저 쓰면 닫을 때 지우기 전에 프로세스가 끝났을 때(Ctrl+C) 다음 명령들이 그 쿠키를 30분 동안 이어 썼다 (쓰고 지우기까지 약 1ms). 본문 없는 429 첫 화면은 goto 가 실패해 이 확인까지 오지 않아 쿠키를 남기지 않는다(`_launch`). 5xx 첫 화면의 쿠키로 덮으면 다시 넣은 방문자 쿠키의 첫 화면 시각이 새것이 되어, 다음
        명령들이 장애 화면에서 받은 쿠키를 30분 동안 썼다.
        """
        if status is not None and status >= 400 and status != 429:
            return False
        return _origin(self.page_url) == _origin(self.options.entry_url)

    def record_entry_requests(self) -> None:
        """첫 화면을 열며 페이지가 보낸 사이트 API 요청을 요청 제한에 남긴다. 연 뒤(`finish_entry`)와 열다 실패했을 때 부른다.

        남기지 못해도(상태 폴더 오류) 경고만 남긴다. 에러를 내면 그 뒤의 판정(429 쿨다운 저장, 5xx 재시도, 4xx 차단)이나 열다
        난 원래 에러(시간 초과 등)를 덮었다 (429 첫 화면에 쿨다운이 남지 않고 INTERNAL_ERROR 로 끝났다).
        """
        if not self.options.count_entry_requests or self.options.rate_limiter is None:
            return
        try:
            self.options.rate_limiter.record(self.routed)
        except (StateDirError, OSError) as error:
            logger.warning('첫 화면 요청을 요청 제한에 남기지 못했습니다: %s', error)

    def raise_if_entry_failed(self) -> None:
        """이 소스의 첫 화면이 `FAILED_ENTRY_MEMORY_SECONDS` 안에 다시 열어도 같은 이유로 실패했으면 그 에러를 다시 낸다.

        같은 소스라도 proxy(주지 않았으면 환경 변수 프록시)·verify 가 다른 클라이언트는 따로 본다 (`_failure_key`). 프록시를
        고쳐 새로 만든 클라이언트가 앞 클라이언트의 ProxyError 를 받았다. 기억해 둔 예외 객체가 아니라 같은
        내용의 새 객체로 던진다 (이유는 본문 주석).
        """
        failed = _failed_entries.get(self._failure_key())
        if failed is not None and time.monotonic() - failed[0] < FAILED_ENTRY_MEMORY_SECONDS:
            error = failed[1]
            # 기억해 둔 에러(`_failed_entries`)와 같은 종류·속성의 새 객체를 `__init__` 을 거치지 않고 만들어 던진다. 같은 객체를
            # 다시 던지면 던질 때마다 traceback 에 프레임이 이어 붙어 길어지고(2026-11-09 확인: 2·4·6개), 앞에서 붙은
            # `__context__` 가 남아 관계없는 에러가 이어지며, 여러 스레드가 함께 던지면 `__traceback__` 이 섞였다 (스레드 2개 4만 번에 1번). `copy.copy` 는 `args` 로 `__init__` 을 다시 불러 인자가 다른 BaenggipyoError 서브클래스
            # (code·message…)에서 TypeError 가 난다
            fresh = BaseException.__new__(type(error), *error.args)
            fresh.__dict__.update(error.__dict__)
            fresh.__cause__ = error.__cause__
            fresh.__suppress_context__ = error.__suppress_context__
            raise fresh

    def remember_if_fatal(self, error: Exception) -> None:
        """첫 화면을 띄우다 난 에러 중 재시도하지 않는 것(시간 초과·다른 출처·차단 화면·60초 넘게 기다리라는 5xx·프록시·Chromium)을 기억한다.

        재시도하는 실패(ConnectError, 5xx HTTPStatusError)는 기억하지 않는다. 429 쿨다운은 요청 제한 상태 파일이 기억한다.
        상태 폴더 오류(`StateDirError`: 잠금·바꿔치기 경합 등)도 다시 열면 풀릴 수 있어 기억하지 않는다.
        한 요청이 이 실패를 잡고 다음 일로 넘어가도 기억해 두면 다음 요청이 다시 열지 않는다. `EntryPageError`·`BROWSER_UNAVAILABLE` 은
        BaenggipyoError 다.
        """
        if isinstance(error, ProxyError) or (
            isinstance(error, BaenggipyoError) and not isinstance(error, (RateLimitedError, StateDirError))
        ):
            _failed_entries[self._failure_key()] = (time.monotonic(), error)

    def _failure_key(self) -> _FailureKey:
        """`_failed_entries` 의 키. `proxy` 를 주지 않았으면 브라우저가 읽는 환경 변수 프록시(`_proxy_settings`)도 넣는다.

        넣지 않았을 때는 환경 변수(`HTTPS_PROXY` 등)를 고친 뒤 새로 만든 클라이언트가 앞 실패를 그대로 받았다 (2026-11-10
        실측: 죽은 `HTTPS_PROXY` 로 실패한 뒤 지워도 0.0초 만에 같은 ProxyError). 환경 변수가 없을 때 Chromium 이
        보는 OS 프록시 설정(GNOME·Windows·macOS)을 바꾼 것은 알 수 없어 `FAILED_ENTRY_MEMORY_SECONDS` 가 지나야 다시 연다.
        """
        env_proxies = (
            None if self.options.proxy is not None else tuple(sorted(urllib.request.getproxies_environment().items()))
        )
        return self.options.source, self.options.proxy, env_proxies, self.options.verify

    def on_send(self, url: str) -> None:
        # 첫 화면 탭에서 `count_request` 가 센 우리 요청을 같은 조건(사이트 API 호스트)으로 센다. 첫 화면 JS 가 더 부른
        # 요청 수를 routed 에서 빼려는 것이라, 응답이 아니라 보낼 때 세야 실패한 요청도 routed 와 맞는다
        if urlsplit(url).netloc in self.options.api_hosts:
            self.ours += 1

    def on_response(self, status: int) -> None:
        if status in self.options.discard_statuses:
            self.discard = True

    def save(self, cookies: Sequence[Mapping[str, Any]]) -> None:
        """쿠키 상태를 남긴다. 쿠키는 다음 명령을 빠르게 할 뿐이라 못 써도 명령은 실패시키지 않는다.

        페이지가 받은 쿠키를 모두 남긴다. 광고·분석용 제3자 쿠키도 들어 있을 수 있지만 남긴 쿠키로 띄우면 빈 페이지라
        그 호스트로 요청이 나가지 않아 어디로도 보내지지 않는다. 로그인 없는 익명
        방문자 쿠키라 거르지 않고, 상태 폴더의 다른 파일처럼 기본 권한으로 쓴다.
        """
        if not self.options.keep_cookies:
            return
        path = _state_path(self.options.source)
        try:
            write_json_atomic(path, {
                'opened_at': self.opened_at, 'page_url': self.page_url, 'cookies': [dict(cookie) for cookie in cookies],
            })
        except OSError as error:
            logger.warning('브라우저 쿠키를 남기지 못했습니다 (%s): %s', path, error)

    def discard_saved(self, opened_at: float) -> None:
        """남긴 쿠키를 버린다. 파일의 첫 화면 시각이 `opened_at` 보다 늦으면 다른 프로세스가 새로 연 것이라 두고 간다.

        같은 쿠키로 함께 뜬 두 명령이 둘 다 403 을 받으면, 먼저 버린 뒤 다음 명령이 새로 받은 쿠키를 나중 명령이 지웠다
       . 읽고 지우는 사이는 잠그지 않는다. 겹쳐도 다음 명령이 첫 화면을 다시 열 뿐이다.
        """
        if not self.options.keep_cookies:
            return
        current_opened_at = _load_state(self.options.source).get('opened_at')
        if isinstance(current_opened_at, (int, float)) and current_opened_at > opened_at:
            return
        with suppress(OSError):
            _state_path(self.options.source).unlink()

    def finish(self, cookies: Sequence[Mapping[str, Any]] | None) -> None:
        """첫 화면을 연 뒤 페이지 JS 가 더 부른 요청을 요청 제한에 남기고, 쿠키를 남기거나 버린다.

        브라우저가 이미 죽어 쿠키를 읽지 못했으면(`cookies` 가 None) 요청 수와 버리기만 하고 남기지 않는다 (`_close`).

        남기거나 버릴 때 그사이 다른 baenggipyo 프로세스가 한 일을 덮지 않는다. 파일이 없으면 다른 프로세스가 403 을 받아 버린
        것이다 (첫 화면을 새로 열었으면 `finish_entry` 전에 바로 남겼고, 남긴 쿠키로 띄웠으면 그 파일이 있었다). 파일의 첫 화면 시각이 더 늦으면
        다른 프로세스가 첫 화면을 새로 연 것이다. 덮으면 거부된 쿠키나 옛 쿠키가 되살아나 다음 명령들이 30분 동안 이어 썼다
       . 읽고 쓰는 사이는 잠그지 않는다. 겹쳐도 다음 명령이 403 을 받아 다시 버린다.

        요청 수를 남기지 못해도(상태 폴더 오류) 경고만 남기고 쿠키 남기기·버리기는 한다. 에러를 내면 403 으로 정한 버리기를 건너뛰어
        거부된 쿠키를 다음 명령들이 30분 동안 이어 썼다 (2026-11-24 리뷰, 가짜 rate_limiter 로 재현).

        `extra` 에는 첫 화면 탭에서 보낸 우리 fetch 가 같은 출처로 넘겨진(redirect) 횟수도 든다. 넘겨질 때마다 서버로 요청이
        실제로 한 번 더 나가므로 세는 것이 맞다. 예전 httpx 도 redirect 마다 요청 훅을 불러 셌다 (`httpx/_client.py`
        `_send_handling_redirects`, 2026-11-10 로컬 확인).
        """
        extra = self.routed - self.routed_at_open - self.ours
        if extra > 0 and self.options.rate_limiter is not None:
            try:
                self.options.rate_limiter.record(extra)
            except (StateDirError, OSError) as error:
                logger.warning('첫 화면 뒤 페이지 요청을 요청 제한에 남기지 못했습니다: %s', error)
        if self.discard:
            self.discard_profile = True
            self.discard_saved(self.opened_at)
            return
        if not self.options.keep_cookies or cookies is None or self.leave_saved or not self.page_url:
            return
        current = _load_state(self.options.source)
        current_opened_at = current.get('opened_at')
        if not current:
            return
        if isinstance(current_opened_at, (int, float)) and current_opened_at > self.opened_at:
            return
        self.save(cookies)

    def log_opened(self, started: float, how: str, cookies: Sequence[Mapping[str, object]]) -> None:
        ready = ''
        if self.options.ready_cookie is not None:
            ready = f', {self.options.ready_cookie} {"있음" if _has_cookie(cookies, self.options.ready_cookie) else "없음"}'
        logger.info(
            '브라우저 준비 %.2f초 (%s, 쿠키 %d개%s, 첫 화면을 연 지 %d분)',
            time.perf_counter() - started, how, len(cookies), ready, (time.time() - self.opened_at) // 60,
        )


# 브라우저 스레드의 일이 끝나기를 기다리며 Ctrl+C 를 확인하는 간격 (`BrowserTransport._wait`). 끝까지 한 번에 기다리면
# Windows 에서는 그 기다림(lock)이 Ctrl+C 로 끊기지 않는다. 일이 끝나면 바로 돌아와 요청이 늦어지지는 않는다
WAIT_POLL_SECONDS: float = 0.1


class _BrowserThread:
    """한 transport(비동기 transport 가 감싼 것 포함)의 브라우저 일을 받은 차례대로 하는 데몬 스레드.

    patchright 동기 API 는 asyncio 루프가 도는 스레드에서 부를 수 없고, 부를 때마다 그 스레드에 자기 asyncio 루프를 '도는
    루프'로 남기며, 한 번 띄운 브라우저는 띄운 스레드에서만 다룰 수 있다 (2026-11-09 실측: 남은 루프 때문에 같은 스레드에서
    `asyncio.run` 이 실패하고 다른 소스의 브라우저를 띄우지 못했다). 그래서 브라우저 일은 늘 이 스레드에서만 한다. 부른
    스레드에는 아무것도 남지 않아 CLI 메인 스레드, asyncio 루프 안(Jupyter), 여러 작업 스레드 어디서 불러도 같다.

    데몬 스레드라 프로세스가 끝날 때 기다리지 않는다. Ctrl+C 로 끊긴 명령은 하던 브라우저 일(첫 화면이면 Chromium 띄우기에 문서 40초·load 10초에 소스별 쿠키 기다림(기본 5초, 스카이스캐너 15초)과
    스카이스캐너의 홈 기다림 3초까지)을 기다리지 않고 바로 끝나고, 드라이버(node)는 파이프가 끊기면 Chromium 을 닫고 끝난다.
    """

    def __init__(self) -> None:
        self._jobs: queue.SimpleQueue[tuple[Callable[..., Any], tuple[Any, ...], Future[Any]] | None] = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._run, name='baenggipyo-browser', daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while (job := self._jobs.get()) is not None:
            function, args, future = job
            # 차례를 기다리는 사이 취소된 일(비동기 요청의 `asyncio.wait_for` 시간 초과 등, `AsyncBrowserTransport.handle_async_request`)은 돌리지
            # 않는다. 돌리면 아무도 기다리지 않는 요청을 사이트로 보내고, 취소된 Future 에 결과를 넣다 InvalidStateError 로 이
            # 스레드가 끝난다. 돌기 시작한 일은 취소되지 않는다
            if future.set_running_or_notify_cancel():
                try:
                    result = function(*args)
                except BaseException as error:
                    # 부른 스레드가 `future.result()` 로 그대로 받는다
                    future.set_exception(error)
                else:
                    future.set_result(result)
            # 다음 일을 기다리는 동안 마지막 일(transport 의 메서드)을 붙잡으면 닫지 않은 transport 가 메모리에서 정리되지
            # 않았다. 브라우저 자체는 닫아야 끝난다 (`BrowserTransport` docstring)
            del job, function, args, future
            result = None

    def submit(self, function: Callable[..., _R], *args: Any) -> 'Future[_R]':
        future: Future[_R] = Future()
        self._jobs.put((function, args, future))
        return future

    def stop(self) -> None:
        """앞에 넣은 일을 마치면 스레드를 끝낸다."""
        self._jobs.put(None)


class BrowserTransport(BaseTransport):
    """사이트 첫 화면을 연 브라우저 페이지에서 요청을 `fetch` 로 보낸다 (동기 `Client` 용).

    닫아야(`close`, 클라이언트의 `with`) 브라우저 스레드와 Chromium 이 끝나고 쿠키가 남는다. 닫지 않으면 프로세스가 끝날
    때까지 남는다. 메모리에서 정리될 때 자동으로 닫지는 않는다 (브라우저 일은 브라우저 스레드에서만 할 수 있어서다).

    Args:
        options: 설정 (`BrowserOptions`).
    """

    def __init__(self, options: BrowserOptions) -> None:
        self._state = _BrowserState(options)
        self._session: StealthySession | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        # `persistent_profile` 의 기록 폴더와 그 잠금. `_launch` 가 얻고 `_close` 가 푼다
        self._profile: _ProfileDir | None = None
        # 탭마다 CORS 에 막힌 응답 기록
        self._blocked: dict[Page, _BlockedResponses] = {}
        # 브라우저 일을 하는 스레드. 첫 브라우저 일 때 `_submit` 이 띄운다
        self._thread: _BrowserThread | None = None
        # `_thread`·`_closed` 와 일 넣기를 함께 지킨다. 넣기를 잠금 밖에서 하면 다른 스레드의 close 가 넣은 끝 표시 뒤에 들어가
        # 영영 돌지 않고 기다림이 멈췄다
        self._thread_lock = threading.Lock()
        self._closed = False
        # 기다리다 Ctrl+C·비동기 취소로 끊긴 브라우저 일 (`_abandon`). 그중 아직 도는 일이 있으면 `close` 가 기다리지 않는다
        self._abandoned: list[Future[Any]] = []

    def _submit(self, function: Callable[..., _R], *args: Any) -> 'Future[_R]':
        """브라우저 일을 이 transport 의 브라우저 스레드(`_BrowserThread`)에 넣는다.

        브라우저 스레드는 `_handle_request`·`_close` 만 돌리고 둘 다 요청을 다시 보내지 않아, 이 함수를 브라우저 스레드에서
        부르는 일은 없다 (브라우저 스레드가 자기 일이 끝나기를 기다리면 멈춘다).

        Raises:
            RuntimeError: 닫은 뒤에 불렸을 때. httpx 가 닫힌 클라이언트에 내는 것과 같다. 여러 스레드가 같이 쓰다 한 스레드가
                닫은 경우이고, 닫힌 transport 에 브라우저를 다시 띄우지 않는다.
        """
        with self._thread_lock:
            if self._closed:
                raise RuntimeError('닫힌 클라이언트로는 요청할 수 없습니다 (브라우저를 이미 닫았습니다)')
            if self._thread is None:
                self._thread = _BrowserThread()
            return self._thread.submit(function, *args)

    def _wait(self, future: 'Future[_R]') -> _R:
        """브라우저 스레드의 일이 끝나기를 기다린다. Ctrl+C 를 `WAIT_POLL_SECONDS` 마다 확인한다.

        기다리다 Ctrl+C 로 끊기면 브라우저 일은 그대로 두고 바로 돌아간다. patchright 객체는 브라우저 스레드에서만 다룰 수
        있어 다른 스레드에서 끊을 수 없다. 그 뒤의 `close` 는 그 일이 아직 돌고 있으면 기다리지 않는다 (`_abandon`).
        """
        try:
            while not future.done():
                wait((future,), timeout=WAIT_POLL_SECONDS)
        except BaseException:
            self._abandon(future)
            raise
        return future.result()

    def _abandon(self, future: 'Future[Any]') -> None:
        """기다리다 끊긴(Ctrl+C·비동기 취소) 브라우저 일을 남긴다. 이미 끝난 일은 걷어 낸다.

        켜고 끄는 표시로 두었을 때는 끊긴 일이 끝난 뒤에도 다음 일이 성공해야만 꺼져, 다음 일이 에러로 끝나거나 더 부르지 않으면
        아무 일도 돌지 않는데 `close` 가 닫기를 기다리지 않았다. 비동기 클라이언트는 닫기 전에 프로세스가 끝나 쿠키를 남기지도,
        403 으로 정한 버리기를 하지도 못했다 (시간 초과 뒤 에러·시간 초과 뒤 요청 없음에서 `aclose` 0초).
        일마다 끝났는지 보므로, 다른 일이 끝났다고 아직 도는 끊긴 일을 기다리지도 않는다.
        """
        with self._thread_lock:
            self._abandoned = [job for job in self._abandoned if not job.done()] + [future]

    def _open_page(self) -> 'Page':
        if self._page is not None:
            return self._page
        self._state.raise_if_entry_failed()
        try:
            return self._launch()
        except Exception as error:
            self._state.remember_if_fatal(error)
            raise

    def _watch_blocked(self, context: 'BrowserContext', page: 'Page') -> None:
        """탭의 막힌 응답(`_BlockedResponses`)을 받아 두고, 우리 요청의 CORS 사전 요청을 요청 제한에 센다 (`count_preflight`)."""
        blocked = _BlockedResponses()
        session = context.new_cdp_session(page)
        blocked.listen(session)
        _count_preflights(session, self._state)
        session.send('Network.enable')
        self._blocked[page] = blocked

    def _launch(self) -> 'Page':
        # 모듈 docstring 대로 브라우저를 쓸 때만 불러온다
        from scrapling.fetchers import StealthySession

        state = self._state
        options = state.options
        state.reset()
        started = time.perf_counter()
        # 쿠키 파일은 `keep_cookies` 일 때만 읽는다. 아니면 늘 첫 화면을 연다
        saved = _load_state(options.source) if options.keep_cookies else {}
        cookies: Sequence[Mapping[str, Any]] | None = (
            usable_cookies(saved, options.entry_url, options.ready_cookie, time.time()) if options.keep_cookies else None
        )
        from_saved = cookies is not None
        entry_status: int | None = None
        entry_document_retry_after: str | None = None
        try:
            session_options = state.session_options()
            if options.persistent_profile:
                # 잠금·폴더는 `_launch` 한 번에 한 벌이다. 재시도가 `_launch` 를 다시 부르면 `_close` 가 푼 뒤 다시 얻는다
                self._profile = _acquire_profile(options.source)
                session_options['user_data_dir'] = str(self._profile.path)
            self._session = StealthySession(**session_options)
        except BaseException:
            # 잠금을 얻은 뒤 세션을 만들다 실패해도 잠금·임시 폴더를 남기지 않는다
            self._close()
            raise

        # 첫 화면 탭의 main frame 문서가 받은 마지막 4xx·5xx (상태, Retry-After). 본문 없는 4xx·5xx 면 goto 가 응답을 돌려주지
        # 않고 실패해(`HTTP_RESPONSE_CODE_NET_ERROR`) 이 값으로 첫 화면을 판정한다. 리다이렉트 뒤의 문서면 마지막 것이 남는다
        failed_document: list[tuple[int, str | None]] = []

        def on_entry_response(response: 'PageResponse') -> None:
            if response.status < 400:
                return
            # 첫 화면 탭의 main frame 문서 응답인지 본다 (iframe 광고 문서는 아니다). 드라이버에 묻지 않는 속성만 본다.
            # `response` 이벤트 리스너 안이라 예외를 내지 않는다 (`count_preflight` 와 같은 이유). patchright 의 `request.frame`
            # 은 서비스 워커 요청과 frame 이 생기기 전에 보낸 이동 요청(새 창의 첫 이동)에서 Error 를 낸다. 첫 화면 탭에서는
            # 생기지 않지만 (`session_options` 의 서비스 워커 주석) 생기면 main frame 문서가 아니라고 본다
            request = response.request
            try:
                main_document = request.is_navigation_request() and request.frame.parent_frame is None
            except _playwright_error():
                main_document = False
            retry_after = response.headers.get('retry-after')
            if main_document:
                failed_document[:] = [(response.status, retry_after)]
            if response.status == 429:
                state.on_entry_rate_limited(response.url, retry_after, main_document)

        try:
            self._session.start()
            context: BrowserContext = self._session.context
            page = context.new_page()
        except (_playwright_error(), OSError) as error:
            # OSError: patchright 드라이버(node)를 실행하지 못했다 (실행 권한 없음·지워짐·보안 프로그램이 막음). 잡지 않으면
            # INTERNAL_ERROR 에 asyncio 'Future exception was never retrieved' traceback 이 stderr 로 더 나갔다 (2026-11-09 실측)
            self._close()
            raise _launch_error(error) from error
        except BaseException:
            # 그 밖의 이유로 끝나도 띄운 브라우저를 남기지 않는다. 남기면 다음 `_launch` 가 `_session` 을 덮어
            # Chromium 이 프로세스가 끝날 때까지 남았다. 아래 두 블록도 같다
            self._close()
            raise
        # 사이트 첫 화면 문서를 기다리는 중인지. 그때의 시간 초과만 첫 화면 실패다 (`_entry_error`)
        opening_entry = False
        try:
            page.on('request', lambda page_request: state.count_request(page_request.resource_type, page_request.url))
            if cookies is not None:
                state.page_url, state.opened_at = saved['page_url'], saved['opened_at']
                try:
                    # add_cookies 는 SetCookieParam(TypedDict)을 받는데 patchright 가 그 타입을 공개하지 않는다 (`_impl` 에만
                    # 있다). 쿠키는 baenggipyo 가 남긴 JSON 이라 Mapping 이고, 내부 모듈을 import 해 cast 하면 patchright 를 올릴 때
                    # 깨질 수 있어 오류 코드를 붙여 둔다. 아래 returning 도 같다
                    context.add_cookies(cookies)  # type: ignore[arg-type]
                except _playwright_error():
                    # 브라우저가 남긴 쿠키를 받지 않으면 다음 시도·명령도 같으니 버린다. 재시도는 첫 화면부터 연다. 버리는 것은
                    # 이때뿐이다. 빈 페이지 띄우기의 시간 초과처럼 브라우저가 잠깐 멈춘 실패에 버리면 멀쩡한 쿠키를 버리고
                    # 재시도가 첫 화면부터 열었다 (한 번 일부러 시간 초과를 내니 파일이 지워지고 첫 화면을 다시 엶)
                    state.discard_saved(saved['opened_at'])
                    raise
                how = '남긴 쿠키'
            else:
                returning: Sequence[Mapping[str, object]] = []
                if options.persistent_profile:
                    # 기록 폴더에서 온 쿠키는 파일 쿠키처럼 넣을 것이 없다. 첫 화면을 열기 전에 이미 있는 쿠키를 '이어 쓴 쿠키' 로
                    # 두어, `ready_cookie` 가 이미 있어도 사이트가 쿠키를 새로 심거나 바꿀 때까지 기다리게 한다 (`_EntryCookies`)
                    returning = context.cookies()
                elif options.keep_cookies:
                    returning = returning_cookies(saved, options.entry_url, time.time())
                    if returning:
                        try:
                            context.add_cookies(returning)  # type: ignore[arg-type]  # 위 남긴 쿠키와 같은 이유
                        except _playwright_error() as error:
                            # 브라우저가 받지 않으면 빈 채로 연다. 첫 화면을 연 뒤 남기는 쿠키가 이 쿠키를 덮는다
                            logger.warning('남긴 방문자 쿠키를 넣지 못해 빈 채로 엽니다: %s', _first_line(error))
                            returning = []
                static = _watch_entry(context, page, state)
                page.on('response', on_entry_response)
                opening_entry = True
                response = page.goto(state.options.entry_url, wait_until='domcontentloaded', timeout=ENTRY_TIMEOUT_MS)
                opening_entry = False
                entry_status = response.status if response else None
                entry_document_retry_after = response.headers.get('retry-after') if response else None
                # 첫 화면 문서가 4xx·5xx 면 load·ready_cookie 를 기다리지 않고 바로 `finish_entry` 로 멈춘다. 기다리면 그동안
                # 막힌 사이트에 페이지 요청이 더 나가고, 오류 화면이 심지 않는 ready_cookie 를 5초 기다렸다 (2026-11-10 로컬
                # 실측: 503 첫 화면이 ready_cookie 가 있으면 5.6초, 없으면 0.5초에 실패). 첫 화면 API 가 429 를 받았을
                # 때(`entry_429`)도 같은 이유로 쿠키를 더 기다리지 않는다
                entry_failed = entry_status is not None and entry_status >= 400
                if not entry_failed:
                    try:
                        page.wait_for_load_state('load', timeout=ENTRY_LOAD_TIMEOUT_MS)
                    except _playwright_timeout_error():
                        logger.info(
                            '첫 화면(%s) load 가 %g초 안에 끝나지 않아 기다리지 않고 넘어갑니다 (사이트 밖 스크립트가 늦을 수 있음)',
                            state.options.entry_url, ENTRY_LOAD_TIMEOUT_MS / 1000,
                        )
                cookies = context.cookies()
                deadline = time.monotonic() + options.ready_timeout
                # 시간 안에 다 심지 않아도 멈추지 않고 이 쿠키로 요청한다. ready_cookie 가 없으면 남긴 쿠키는 다음 명령이 쓰지
                # 않아 첫 화면을 다시 연다 (`usable_cookies`)
                entry_cookies = _EntryCookies(state.options.ready_cookie, returning)
                while (
                    not entry_failed and not state.entry_429 and not entry_cookies.ready(cookies, time.monotonic())
                    and time.monotonic() < deadline
                ):
                    page.wait_for_timeout(READY_COOKIE_POLL_MS)
                    cookies = context.cookies()
                # 새로 연 첫 화면이 제대로 열렸을 때만(위 ready 루프가 건너뛰는 조건과 같다) 홈이 스스로 보내는 요청이 끝나길 더
                # 기다린다. 기다리는 동안 늦게 온 429 도 `static` 이 붙은 채라 센다
                if options.entry_settle_seconds > 0 and not entry_failed and not state.entry_429:
                    page.wait_for_timeout(options.entry_settle_seconds * 1000)
                    cookies = context.cookies()
                if static is not None:
                    static.detach()
                page.remove_listener('response', on_entry_response)
                state.page_url, state.opened_at = page.url, time.time()
                how = f'첫 화면 HTTP {entry_status or "-"}, 페이지 요청 {state.routed}건, 이어 쓴 쿠키 {len(returning)}개'
            # 마지막으로 연 페이지 주소를 서버에 묻지 않고 브라우저 안에서 빈 페이지로 띄운다 (Referer 가 그 주소다). 새로 연
            # 첫 화면도 쿠키를 다 받은 뒤 빈 페이지로 바꿔, 명령 동안 사이트 JS 가 API·지도 타일을 더 부르지 않게 한다.
            # `stay_on_entry_page` 면 새로 연 첫 화면은 바꾸지 않고 그 탭에서 그대로 요청한다 (남긴 쿠키로 띄운 탭은 열린 첫 화면이
            # 없어 위처럼 빈 페이지를 띄운다)
            if not (options.stay_on_entry_page and not from_saved):
                page.route(lambda url: url == state.page_url, _fulfill_blank)
                page.goto(state.page_url, wait_until='commit')
                page.unroute_all(behavior=UNROUTE_BEHAVIOR)
            self._watch_blocked(context, page)
        except BaseException as error:
            if (
                opening_entry and failed_document and isinstance(error, _playwright_error())
                and HTTP_RESPONSE_CODE_NET_ERROR in _first_line(error)
            ):
                # 본문 없는 4xx·5xx 첫 화면 문서라 goto 가 응답 대신 오류를 냈다. 그 문서의 상태로 본문이 있을 때와 같이 판정한다.
                # `finish_entry` 는 400 이상이면 모든 갈래에서 예외를 내 이 갈래는 늘 여기서 끝난다 (돌아오면 아래 기존 처리로 내려간다).
                # `finish_entry` 가 첫 화면 요청을 세므로 `record_entry_requests` 를 따로 부르지 않는다. `_close` 는 `finish_entry` 가
                # 켠 `discard` 를 보고 쿠키·기록 폴더를 지우므로 그 뒤에 부른다. `_close` 는 컨텍스트가 있을 때만 `finish` 를 불러
                # 지우기를 정하므로 컨텍스트를 먼저 둔다 (두지 않으면 본문 없는 403 뒤에 기록 폴더와 옛 쿠키 파일이 남았다, 2026-11-24
                # 리뷰, 가짜 첫 화면으로 재현). 첫 화면 시각도 지금으로 둔다. 남긴 쿠키로 띄우지 않았으면 0 이라 `discard_saved` 가
                # 옛 쿠키 파일을 다른 프로세스가 새로 연 것으로 보고 지우지 않았다. 본문이 없어 첫 화면 JS 가 돌지 않아 `ready_cookie` 가
                # 생기지 않고 남겨도 `usable_cookies` 가 쓰지 않아 쿠키는 남기지 않는다 (`page_url` 이 비어 `finish` 가 남기지 않는다)
                status, retry_after = failed_document[0]
                self._context = context
                state.opened_at = time.time()
                try:
                    state.finish_entry(status, retry_after)
                except BaseException as entry_error:
                    self._close()
                    raise entry_error from error
            self._close()
            state.record_entry_requests()
            if not isinstance(error, _playwright_error()):
                raise
            raise _entry_error(error, state.options.entry_url, opening_entry) from error
        # 탭(`_page`)은 첫 화면이 제대로 열린 것을 확인한 뒤에 내놓는다. 컨텍스트는 실패했을 때 `_close` 가 쿠키를 버리도록
        # 먼저 둔다 (`finish`)
        self._context = context
        if options.keep_cookies and not from_saved and state.keeps_entry_cookies(entry_status):
            # 새로 연 첫 화면의 쿠키는 확인(`finish_entry`) 전에 바로 남긴다. 같은 명령 안에서 새로 만든 같은 소스 클라이언트가
            # 첫 화면을 다시 열지 않게 하고, 429 로 멈춰도 쿨다운 뒤 남긴 쿠키로 요청 1건만 보내 풀렸는지
            # 보게 한다 (본문 없는 429 첫 화면은 goto 가 실패해 쿠키를 남기지 않는다. 위 `except` 갈래). 확인 뒤에 남겼을 때는 쿠키 파일이 없던 상태(첫 실행, 403 으로 버린 뒤)에서 429 로 멈추면 `finish` 가
            # 파일이 없다고 남기지 않았다. 4xx·5xx·다른 출처면 남기지 않는다 (`keeps_entry_cookies`). 4xx·다른
            # 출처는 `finish_entry` 가 discard 를 켜 `_close` 가 옛 파일을 지우고, 5xx 는 옛 파일을 그대로 둔다(`leave_saved`).
            # 명령이 끝날 때(`close`) 그때의 쿠키로 다시 남긴다
            state.save(cookies)
        try:
            state.finish_entry(entry_status, entry_document_retry_after)
        except BaseException:
            self._close()
            raise
        self._page = page
        state.log_opened(started, how, cookies)
        return page

    def handle_request(self, request: Request) -> Response:
        # 브라우저 일을 브라우저 스레드에서 부르고 끝나기를 기다린다 (`_submit`, `_wait`)
        return self._wait(self._submit(self._handle_request, request))

    def _handle_request(self, request: Request) -> Response:
        # 모든 요청을 첫 화면 탭에서 보낸다. 요청 주소가 첫 화면과 다른 출처면 그 서버가 첫 화면 출처의 CORS 를 허용해야 한다
        page = self._open_page()
        arguments, timeout = _fetch_arguments(
            request, self._state.options.timeout, self._state.options.fetch_credentials,
        )
        self._state.on_send(str(request.url))
        page_headers: dict[str, dict[str, str]] = {}

        def on_page_response(page_response: 'PageResponse') -> None:
            if page_response.request.resource_type == 'fetch':
                page_headers[page_response.url] = page_response.headers

        try:
            blocked = self._blocked[page]
            page.on('response', on_page_response)
            try:
                result = page.evaluate(FETCH_SCRIPT, arguments)
                deadline = time.monotonic() + BLOCKED_RESPONSE_WAIT_SECONDS
                while (
                    result.get('error') == 'TypeError' and not blocked.settled(result['requestUrl'])
                    and time.monotonic() < deadline
                ):
                    page.wait_for_timeout(BLOCKED_RESPONSE_POLL_MS)
                if 'bodyId' in result:
                    # `FETCH_SCRIPT` 가 브라우저 안에 둔 큰 본문을 `BODY_CHUNK_BYTES` 씩 읽고 브라우저 안의 것은 지운다
                    body_id = result.pop('bodyId')
                    size = result['size']
                    try:
                        result['content'] = b''.join(
                            base64.b64decode(
                                page.evaluate(BODY_SLICE_SCRIPT, [body_id, start, min(start + BODY_CHUNK_BYTES, size)])
                            )
                            for start in range(0, size, BODY_CHUNK_BYTES)
                        )
                    finally:
                        # 읽다 실패해도 지운다. 탭이 이미 닫혔으면 본문도 함께 사라졌다
                        with suppress(_playwright_error()):
                            page.evaluate(BODY_DROP_SCRIPT, body_id)
            finally:
                page.remove_listener('response', on_page_response)
        except _playwright_error() as error:
            # 탭·브라우저가 닫혔으면 다시 써도 같은 에러다. 큰 본문을 나눠 읽다가 본문이 없어졌으면 탭은 살아 있어도
            # 페이지가 다른 곳으로 넘어가 evaluate 세계가 바뀐 것이라, 이 탭이 아직 첫 화면인지 알 수 없다. 어느 쪽이든 닫고 재시도에서
            # 남긴 쿠키로 다시 띄운다
            self._close()
            raise ConnectError(f'브라우저 요청 실패: {_first_line(error)}', request=request) from error
        response = _to_response(request, result, timeout, page_headers, blocked.pop(result['requestUrl']))
        self._state.on_response(response.status_code)
        return response

    def close(self) -> None:
        """브라우저를 닫는다. 브라우저 스레드가 앞의 일을 마친 뒤 닫고(쿠키를 남기고) 끝나며, 그때까지 기다린다.

        기다리다 Ctrl+C·비동기 취소로 끊긴 브라우저 일이 아직 돌고 있으면 기다리지 않는다 (`_abandon`). CLI 는 바로 끝나 그
        명령에서 바뀐 쿠키는 남지 않는다 (첫 화면을 새로 열었으면 연 즉시 남겼다). 오래 도는 프로세스(Jupyter)에서는 브라우저
        스레드가 하던 일을 마친 뒤 닫는다.
        """
        with self._thread_lock:
            thread, self._thread = self._thread, None
            self._closed = True
            if thread is None:
                return
            # 브라우저 스레드에서 `close` 를 부르는 곳은 없다 (`_submit` 과 같다)
            future = thread.submit(self._close)
            thread.stop()
        if not any(not job.done() for job in self._abandoned):
            self._wait(future)

    def _close(self) -> None:
        session, context, profile = self._session, self._context, self._profile
        self._session = self._context = self._page = self._profile = None
        self._blocked.clear()
        try:
            if session is None:
                return
            try:
                if context is not None:
                    cookies: Sequence[Mapping[str, Any]] | None = None
                    try:
                        cookies = context.cookies()
                    except Exception as error:
                        # 브라우저가 이미 죽었으면(메모리 부족·강제 종료) 쿠키를 읽지 못한다. 그래도 finish 는 불러 403 으로 정한
                        # 버리기와 더 나간 요청 수를 남긴다. 한 줄로 부르면 finish 가 불리지 않아 거부된 쿠키 파일이 남고, 재시도와
                        # 다음 명령들이 그 쿠키를 이어 썼다 (Chromium 을 강제 종료해 재현)
                        logger.warning('브라우저 쿠키를 읽지 못했습니다: %s', _first_line(error))
                    self._state.finish(cookies)
            except Exception as error:
                logger.warning('브라우저 쿠키를 정리하지 못했습니다: %s', error)
            finally:
                with suppress(Exception):
                    session.close()
        finally:
            # 기록 폴더는 Chromium 이 닫힌 뒤에 지운다 (쓰는 중에 지우지 않는다). 세션을 만들다 실패해 `session` 이 없을 때도 잠금을
            # 풀고 임시 폴더를 지운다. 잠금을 얻은 폴더는 막혀 `finish` 가 지우기로 정했을 때만 지운다
            if profile is not None:
                profile.release(remove=self._state.discard_profile)


class AsyncBrowserTransport(AsyncBaseTransport):
    """`BrowserTransport` 의 비동기판 (`AsyncClient` 용). 감싼 동기 transport 의 브라우저 스레드에 일을 넘기고 `await` 로 기다린다.

    흐름(첫 화면 열기·요청 보내기)은 동기 transport 와 한 벌이다 (`AsyncBrowserTransport.handle_async_request`). 동시에 보낸 요청
    (`asyncio.gather`)은 브라우저 스레드가 받은 차례대로 하나씩 보낸다.
    탭이 하나라 차례로 보내도 요청 제한 간격(0.5초) 때문에 실제 요청에서는 차이가 없다 (같은 날 가짜 사이트 실측: 8건, 응답
    300ms 에 3.81초).

    설정은 감쌀 동기 transport 를 만들 때 준다 (`AsyncBrowserTransport(BrowserTransport(BrowserOptions(...)))`).

    Args:
        transport: 감쌀 동기 transport. 이 transport 를 닫으면(`aclose`) 함께 닫힌다.
    """

    def __init__(self, transport: BrowserTransport) -> None:
        self._sync = transport

    async def handle_async_request(self, request: Request) -> Response:
        # 비동기 본문(async iterator)은 브라우저 스레드에서 sync read() 로 읽을 수 없어 먼저 읽어 둔다. 읽은 본문은
        # `_fetch_arguments` 가 쓴다
        await request.aread()
        # `BrowserTransport.handle_request` 의 비동기판이다. 브라우저 스레드의 일을 이벤트 루프를 막지 않고 기다린다.
        # 기다리다 취소되면(`asyncio.wait_for` 시간 초과 등) Ctrl+C 를 받은 `_wait` 처럼 하던 브라우저 일은 그대로 두고 바로
        # 돌아가고, 그 뒤의 `close` 는 그 일이 아직 돌고 있으면 기다리지 않는다 (`_abandon`). 아직 차례를 기다리던 일은 돌리지
        # 않는다 (`_BrowserThread._run`). 작업 스레드(`asyncio.to_thread`)에서 `BrowserTransport.handle_request` 로 기다리면
        # 취소돼도 그 스레드가 브라우저 일을 끝까지 기다려, 닫기와 `asyncio.run` 이 그만큼(첫 화면이면 Chromium 띄우기에 문서 40초·load 10초에 소스별 쿠키 기다림(기본 5초, 스카이스캐너 15초)과 스카이스캐너의 홈 기다림 3초까지) 늦었다
        # (8초 걸리는 첫 화면에서 `aclose` 8.04초)
        future = self._sync._submit(self._sync._handle_request, request)
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            self._sync._abandon(future)
            raise

    async def aclose(self) -> None:
        # 닫기는 브라우저 스레드가 쿠키를 남기고 브라우저를 닫을 때까지 기다리므로 작업 스레드에서 해 이벤트 루프를 막지 않는다
        await asyncio.to_thread(self._sync.close)
