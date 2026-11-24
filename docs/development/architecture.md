# Architecture

baenggipyo 의 계층 구조와, 명령 하나가 실행될 때 요청이 흐르는 경로. 형식 규칙은
[code-style.md](code-style.md), provider 모듈 작성 규칙은
[provider-conventions.md](provider-conventions.md), 출력 형식은
[output-contract.md](output-contract.md), 소스 고유 구현은 [네이버](providers/naver.md)·[스카이스캐너](providers/skyscanner.md)
개발 가이드를 본다.

## 계층

```
cli/                 Typer 명령. 인자 검증 → service 호출 → emit / handle_errors
 └─ providers/<source>/service/     여러 엔드포인트를 묶어 공통 항공권 모델로 정규화 (기능별 모듈)
     └─ providers/<source>/api/        소스 API 그대로: client.py (엔드포인트 하나 = 메서드 하나, run_request_*),
                                        request/·response/ 모델
         └─ core/http.py BaseHTTPClient   httpx 클라이언트 + 요청 제한 훅 + 브라우저 설정
             └─ core/browser.py BrowserTransport   요청을 사이트 첫 화면을 연 Chromium 안의 fetch 로 보낸다
```

각 계층은 아래 계층만 안다. 새 소스를 추가하면 `cli/app.py` 의 `limits` 명령 client 목록도 함께 고친다.

- **client** 는 HTTP 호출과 응답 검증만 한다. 응답을 `<Source>{Action}ResponseModel`
  로 검증해 돌려줄 뿐, 통화 환산이나 매진 판정 같은 해석은 하지 않는다.
- **service** 는 client 메서드를 조합해 검색·상세 흐름을 만들고, 소스 응답을
  `core.flight` 의 공통 모델(`Itinerary`, `Offer` 등)로 바꾼다. 매진·만료 운임 거르기, 단위 환산,
  시각·소요 시간 정규화, `BaenggipyoError` 변환이 여기서 일어난다. 함수는 첫 인자로 client 를 받는다
  (`search_flights(client, legs, ...)`).
- **cli** 는 옵션 파싱(`split_csv`, `parse_legs`, `validate_passengers`), client 생성(`with XxxClient() as client`),
  출력(`emit`)만 한다. 비즈니스 로직을 넣지 않는다.

검색 service 가 서버 필터 결과를 어떻게 다루는지는
[서버 필터와 클라이언트 후처리](provider-conventions.md#서버-필터와-클라이언트-후처리)를 따른다.

## BaseHTTPClient

`core/http.py` 의 `BaseHTTPClient(BaseModel, ABC)` 가 모든 provider client 의 기반이다.
pydantic 모델이 **검증된 설정 객체**이자 **httpx 클라이언트 팩토리** 역할을 함께 한다.

- 공통 필드: `url_schema`, `verify`, `proxy`, `timeout`, `rate_limit`.
  `extra='forbid'` 라 오타 난 설정은 생성 시점에 실패한다. `User-Agent` 필드는 없다. 브라우저가 붙인다 ([브라우저 요청](#브라우저-요청)).
- 브라우저 설정은 클래스 변수(`browser_entry_url`·`browser_ready_cookie`·`browser_fetch_credentials` 등)와 property `browser_api_hosts` 로
  서브클래스가 정하고, `browser_transport()` / `async_browser_transport()` 가 그것으로 transport 를 만든다. 서브클래스는 이것을 httpx
  클라이언트의 `transport` 로 넘긴다.
- 서브클래스는 `_client` / `_async_client` 를 `@cached_property` 로 만든다. `close()` 가
  `self.__dict__.pop('_client', None)` 으로 꺼내 닫으므로 이름이 정확히 같아야 한다.
  (`@cached_property` 와 `@abstractmethod` 는 함께 쓸 수 없어 베이스에서는 일반 property 로 선언한다.)
- `aclose()` 는 async 와 sync 를 모두 닫는다. `close()` 는 sync 를 닫고, `with` 안에서 async 메서드를 써 async 클라이언트도 있으면 실행 중인
  이벤트 루프에 그 닫기를 맡긴다 (sync 함수라 기다릴 수 없다. 루프가 이미 끝났으면 참조만 놓는다). 닫은 뒤 다시 접근하면
  새 클라이언트를 만들므로 인스턴스를 재사용할 수 있다.
- `with` / `async with` 를 지원한다. CLI 와 service 호출부는 항상 컨텍스트 매니저로 쓴다.

## 브라우저 요청

`core/browser.py`. 모든 요청을 httpx 의 네트워크 계층이 아니라 **사이트 첫 화면을 연 Chromium(scrapling `StealthySession`, patchright) 안의 `fetch`** 로
보낸다. 사이트가 요청을 사람이 연 브라우저의 것인지 보고 막기도 해서, 첫 화면을 실제로 열어 사이트 JS 가 심은 쿠키·페이지 주소(Referer)·UA·`sec-ch-ua`·
TLS·HTTP/2 지문을 일반 Chrome 과 같게 붙여 보낸다. scrapling 은 그 위에 사이트 JS 의 봇 검사에 걸리는 흔적(`navigator.webdriver` 등)을 감춘다.

- **httpx 는 그대로 쓴다.** 클라이언트는 `Client(transport=self.browser_transport(), event_hooks=self.rate_limit_hooks(), ...)` 로 만들어
  base_url·요청 제한 훅·재시도·`raise_for_status` 가 그대로 돈다. 바뀌는 것은 transport 뿐이다. 프록시·인증서 검증은 브라우저가 맡으므로 httpx 에
  `proxy`·`verify` 를 넘기지 않는다 (넘기면 그 mount 가 transport 를 건너뛴다). 프록시는 `HTTPS_PROXY`·`ALL_PROXY`, 없으면 OS 프록시 설정을 브라우저가
  쓰고, 인증서는 OS 저장소를 본다.
- **헤더.** UA·Origin·Referer·쿠키·Accept-Language·`sec-*` 는 브라우저가 붙이므로 `base_header` 에 넣지 않고, fetch 가 바꿀 수 없는 헤더는
  보내지도 않는다 (`BROWSER_OWNED_HEADERS`). 사이트 웹이 그 API 에 실제로 붙이는 헤더(`Accept`, 스카이스캐너의 `X-Skyscanner-*` 등)만 실측해 넣는다.
- **첫 화면 열기.** 첫 요청 때 브라우저를 띄우고 `browser_entry_url` 을 연다. 문서(DOMContentLoaded)를 40초까지 기다리고(넘으면 `NETWORK_ERROR`),
  load 는 10초까지만 기다리고 넘어간다. 이어 **준비 쿠키**(`browser_ready_cookie`)가 생기고 0.5초 더 바뀌지 않을 때까지 `browser_ready_timeout`(기본 5초) 안에서
  기다린다 (사이트 JS 가 load 뒤에 쿠키를 심고, 몇백 ms 에 걸쳐 나눠 심기도 해서다). 준비 쿠키가 없는 소스는 load 까지만 본다. 시간 안에 안 생겨도 멈추지 않고 요청한다.
- **빈 페이지.** 쿠키를 다 받으면 열린 탭을 서버에 묻지 않고 같은 주소의 빈 페이지로 바꿔(`_fulfill_blank`), 사이트 JS 가 API·지도 타일을 더 부르지 않게 한다.
  그 탭에서 `fetch` 하므로 Referer 는 첫 화면 주소이고, 요청 주소가 첫 화면과 다른 출처면 그 서버가 첫 화면 출처의 CORS 를 허용해야 한다.
  `browser_stay_on_entry_page` 면 바꾸지 않고 열린 첫 화면(홈)을 띄워 둔 채 요청한다.
- **첫 화면이 열리지 않을 때.** 문서가 401·403·451 이면 사이트가 접속을 막은 것이라 `UPSTREAM_ERROR` 에 `blocked: true`(`entry_block_cooldown` 을 켠 소스는 요청 제한에 차단 쿨다운을 걸고 `RATE_LIMITED`, `reason: blocked`), 그 밖의 4xx 는 `status` 만 붙인다. 429(문서나
  사이트 API 요청. `browser_count_entry_requests` 를 끄면 문서만)는 요청 제한이 켜져 있으면 `Retry-After` 를 반영해 쿨다운을 걸고 `RATE_LIMITED` 로 멈춘다. 5xx 는 소스의 재시도 술어대로 재시도하고(1초, 또는 `Retry-After`), 60초
  넘게 기다리라고 하면 바로 `retry_after_seconds` 를 붙여 멈춘다. 다른 출처(로그인·보안 확인 화면)로 넘어가도 `UPSTREAM_ERROR` 다. 본문 없는(`Content-Length: 0`) 4xx·5xx 첫 화면은 Chromium 이 이동(`goto`)을 `net::ERR_HTTP_RESPONSE_CODE_FAILURE` 로 실패시키지만, 그 전에 받은 문서 응답의 상태 코드·`Retry-After` 로 본문이 있을 때와 같이 판정한다. 코드·details·hint 는
  [출력 규약](output-contract.md#브라우저-에러)이 원문이다. 다시 열어도 같은 실패(시간 초과·막힘·다른 출처·프록시·Chromium 문제)는 재시도하지 않고 소스별로 60초 기억해, 같은 명령 안에서 새로
  만든 클라이언트가 브라우저를 다시 띄워 40초를 또 기다리지 않게 한다.
- **쿠키 남기기 (`browser_keep_cookies`, 기본).** CLI 는 명령마다 새 프로세스라 브라우저도 빈 쿠키로 뜬다. 그래서 닫을 때 쿠키를
  `<state_dir>/browser/<source>.json`(`write_json_atomic`)에 남기고, 첫 화면을 연 지 30분(`REFRESH_SECONDS`) 안이고 준비 쿠키가 살아 있으면 다음 명령은 첫 화면을
  다시 열지 않고 남긴 쿠키를 넣은 빈 페이지로 바로 요청한다. 30분이 지나 다시 열 때도 만료 시각이 있는 쿠키(방문자 식별 등)는 먼저 넣는다. 우리 요청이
  `browser_discard_statuses`(기본 403)를 받거나 첫 화면이 4xx·다른 출처면 파일을 지운다. 429 는 지우지 않고(쿨다운 뒤 요청 한 건으로 풀렸는지 본다), 5xx 는 파일을 그대로 둔다.
- **기록 폴더 이어 쓰기 (`browser_persistent_profile`).** 쿠키 파일 대신 브라우저 기록 폴더(`<state_dir>/browser/<source>-profile`)를 명령 사이에 통째로 이어 쓴다
  (쿠키만 옮기지 않고 사이트가 저장한 값 전체가 이어진다). 띄우기 전에 `<source>-profile.lock` 에 기다리지 않는 잠금을 걸고, 다른 명령이 그 폴더를 쓰는 중이면
  이번 명령은 임시 폴더로 띄우고 닫을 때 지운다 (같은 폴더를 Chromium 둘이 쓰면 안 된다). Ctrl+C 로 끊겨 닫지 못하면 프로세스가 끝날 때
  지우지만, Chromium 이 그 뒤에 닫히며 파일을 다시 써 폴더가 남는다 (2026-11-24 실측). 그래서 임시 폴더는 안의 `.baenggipyo-lock` 을 잠가 두고,
  같은 소스의 다음 명령이 시작할 때 60초 넘게 바뀌지 않았고 아무도 잠그지 않은 임시 폴더를 치운다 (`_sweep_temp_profiles`). 폴더에 이미 있는 쿠키는 '이어 쓴 쿠키' 로 보아, 사이트가 쿠키를 새로 심을
  때까지 `browser_ready_timeout` 안에서 기다린다. 우리 요청이 `browser_discard_statuses` 를 받았거나 첫 화면이 막혀(4xx·다른 출처, 429·5xx 는 아님) 끝났으면 닫은 뒤 폴더를 지워
  다음 명령이 새로 시작한다.
- **첫 화면 요청 세기 (`browser_count_entry_requests`, 기본).** 첫 화면이 스스로 부르는 사이트 API 요청(`browser_api_hosts` 로 간 xhr·fetch)과 그 CORS 사전 요청(OPTIONS)은
  요청 제한 훅을 거치지 않아, transport 가 `RateLimiter.record` 로 지금 시각에 센다. 미리 슬롯을 잡지 않고 연 뒤에 세므로 첫 화면을 새로 여는 명령은 분당 상한을 그 묶음만큼
  넘을 수 있고 이어지는 요청이 그만큼 기다린다. 끄면 이 요청을 세지 않고, 하위 요청이 받은 429 도 쿨다운으로 보지 않으며 첫 화면 문서의 상태만 판정에 쓴다. 켜 있으면 우리 요청이 첫 화면 탭에서
  redirect 될 때 넘겨질 때마다도 센다.
- **정적 파일 막기 (`browser_block_entry_resources`, 기본).** 첫 화면을 여는 동안 이미지·글꼴·미디어·스타일시트를 받지 않는다. 쿠키는 문서와 사이트 JS·API 응답으로 생겨 받는 요청 수만 줄고,
  사이트 API 는 막지 않는다. `route` 가 아니라 CDP Fetch 패턴으로 막아야 브라우저가 CORS 사전 요청을 그대로 보낸다. 끄면 막지 않는다.
- **`fetch` 의 `credentials` (`browser_fetch_credentials`).** `'same-origin'`(기본)은 첫 화면과 같은 출처에만 쿠키를 싣는다. API 가 첫 화면과 다른 출처이고 웹이 쿠키를 싣는 소스는
  `'include'` 로 둔다. 그 서버가 사전 요청에 `Access-Control-Allow-Credentials: true` 와 첫 화면 출처를 답해야 한다 (`Allow-Origin: *` 이면 브라우저가 거절한다).
- **응답 받기.** redirect 는 브라우저 안에서 따라간다. 본문이 1MB 를 넘으면 브라우저 안에 두었다가 1MB 씩 나눠 읽는다 (patchright 가 큰 evaluate 결과를 넘기는 시간이 크기보다 훨씬 빨리
  늘어서다). `timeout` 은 응답 헤더가 올 때까지와 본문 조각 사이 간격에 적용한다. CORS 로 막혀 `fetch` 가 이유 없이 실패하면 CDP 네트워크 이벤트(`_BlockedResponses`)로 상태를 알아내
  3xx·4xx·5xx 는 상태로 돌려주고(429 쿨다운이 걸린다), 2xx 인데 CORS 허용이 없거나 사전 요청이 거절됐으면 `CorsBlockedError` 다.
- **스트림 모드.** 요청 extension `STREAM_SECONDS_EXTENSION`(초)이 있으면 2xx 본문(SSE)을 브라우저 안에서 줄 단위로 읽어 **마지막 `data:` 줄 하나만** 돌려준다. 이벤트마다 그때까지의 결과
  전체를 다시 보내는 스트림은 앞 이벤트를 버려도 되는데 파이썬으로 받으면 모두 옮겨야 해서다. 마감은 응답 헤더가 온 때부터 재고, 줄 사이 `timeout` 안에 다음 조각이 없으면 받은 마지막
  줄로 끝낸다(없으면 `ReadTimeout`). 읽다 연결이 끊기면 일부를 쓰지 않고 전송 오류로 올린다. 2xx 가 아니면 스트림으로 읽지 않고 끝까지 읽는다 (네이버 검색이 쓴다).
- **재시도에서 빼는 전송 오류.** 재시도 술어는 전송 오류 중 `RETRYABLE_TRANSPORT_ERRORS`(timeout·연결 오류·원격 프로토콜 오류)만 다시 보낸다. CORS 막힘·프록시 오류·인증서 오류는 다시 보내도
  같아 재시도하지 않는다.
- **스레드·닫기.** 브라우저 일은 transport 마다 띄운 전용 스레드 하나에서 받은 차례대로 한다. 동기·비동기 transport 가 같은 흐름을 쓰고, CLI 와 감시는 동기만 쓴다. `close()` 는 쿠키를 남기고 Chromium 을
  닫은 뒤 끝난다. Ctrl+C 로 끊기면 하던 일을 기다리지 않는다.
- **Chromium 설치.** Chromium 은 pip 로 오지 않아 `baenggipyo install-browser` 로 따로 받는다 (`install_chromium`). 이미 있으면 받지 않고 실제로 한 번 띄워 본다. 명령 도중에 저절로
  받지 않는다 (에이전트 시간 초과 때문). 없거나 못 띄우면 `BROWSER_UNAVAILABLE` 이다. 브라우저를 띄워 명령마다 몇 초 더 걸린다 (2026-11-24 로컬: 네이버 감시 12명령 1분 4초 → 1분 34초).

소스별 설정 (`providers/<source>/api/client.py` 의 클래스 변수). 적지 않은 것은 기본값이다.

| 설정 | 기본값 | 네이버 | 스카이스캐너 |
| --- | --- | --- | --- |
| `browser_entry_url` | (소스가 정함) | `https://flight.naver.com/` | `https://www.skyscanner.co.kr/` |
| `browser_ready_cookie` | 없음 (load 까지) | `NNB` | `_px3` |
| `browser_fetch_credentials` | `'same-origin'` | `'include'` | 기본값 |
| `browser_keep_cookies` | `True` | 기본값 (쿠키 파일 남김) | `False` |
| `browser_persistent_profile` | `False` | 기본값 | `True` |
| `browser_stay_on_entry_page` | `False` | 기본값 | `True` |
| `browser_count_entry_requests` | `True` | 기본값 | `False` |
| `browser_block_entry_resources` | `True` | 기본값 | `False` |
| `browser_ready_timeout` | 5초 | 기본값 | 15초 |
| `browser_entry_settle_seconds` | 0 | 기본값 | 3초 |
| `browser_entry_block_cooldown` | `False` | 기본값 | `True` |

소스에만 있는 이유와 실측은 [네이버](providers/naver.md)·[스카이스캐너](providers/skyscanner.md) 개발 가이드에 있다.

## 요청 제한

`core/ratelimit.py`. 비공식 API 가 봇 차단(429)을 걸기 때문에, **같은 소스를 쓰는 모든
baenggipyo 프로세스가 하나의 상태 파일을 공유**한다. LLM 이 명령을 연달아 실행해도 간격이 지켜진다.

- 상태 파일: `<state_dir>/ratelimit/<source>.json`. `state_dir` 은
  `BAENGGIPYO_STATE_DIR` > `$XDG_CACHE_HOME/baenggipyo` > `~/.cache/baenggipyo`. 환경 변수의 `~` 는 펼치고, 그래도 절대 경로가 아니면 무시한다.
  상대 경로는 프로세스마다 작업 디렉터리 기준으로 풀려 상태 파일을 공유하지 못한다 (XDG 명세도 상대 경로를 무시한다).
- `RateLimiter.reserve()` 가 `flock`(Windows 는 `msvcrt.locking`) 잠금 안에서 다음 슬롯을 예약하고, 실제 대기(`sleep`)는
  잠금 밖에서 한다. 대기 중에 다른 프로세스가 쿨다운을 걸 수 있어 깨어난 뒤 쿨다운을 한 번 더 보고, 걸려 있으면 요청을 보내지 않는다.
  Windows 는 `fcntl` 이 없어 `msvcrt.locking` 으로 같은 잠금을 건다. 또 Windows 는 다른 프로세스가 열고 있는 파일을 바꿔 넣거나 바뀌는 중인 파일을 열면 잠깐
  `PermissionError` 가 나므로, 상태 파일 읽기와 `replace` 는 몇 번 다시 해 본다. 읽기에서 바로 빈 상태로 보면 걸린 쿨다운을 놓치고 그 빈 상태를 다시 써서 지운다.
- `RateLimiter.reserve_ahead()` 는 다음 요청의 슬롯을 지금 예약해 두고, 그 요청의 요청 전 훅은 새로 예약하지 않고 그 슬롯을 쓴다 (미리 잡은 뒤로
  다른 프로세스가 건 쿨다운은 다시 본다). 앞 응답을 놓기 전에 다음 요청의 자리를 확보할 때 쓴다 (스카이스캐너 검색 폴링).
- 상태를 읽기만 하는 곳(대기 뒤 쿨다운 확인, `limits` 와 스카이스캐너 결과의 남은 요청 안내(`cli/skyscanner.py` 의 `budget_notes`)가 부르는 `status()`)은 잠그지도 쓰지도 않는다. 쓰는 쪽이 임시 파일을 `replace` 로 바꿔
  넣어 늘 온전한 한 벌을 읽으므로, 상태 디렉터리에 쓸 권한이 없어도 `limits` 는 된다. 쓰기는 `write_json_atomic` 하나로 하고(판매처 이름표·브라우저 쿠키도 같다),
  임시 파일 이름에 프로세스 ID 와 스레드 ID 를 넣어 잠금 없는 쓰기끼리 임시 파일을 덮어쓰지 않는다.
- 상태 값은 벽시계(`time.time()`)다. 프로세스와 재부팅을 넘어 공유해야 해서 단조 시계를 쓸 수 없다. 그래서 읽을 때 숫자가 아닌 값은 버리고,
  지금부터 `max(max_wait, 60초)` 보다 먼 슬롯(시계가 뒤로 간 흔적)은 무시한다. 남겨 두면 그만큼 모든 요청이 `budget` 으로 거절된다.
  쿨다운은 시계가 뒤로 간 만큼 길어지지만 그대로 둔다 (차단 뒤에는 길게 쉬는 쪽이 안전하고, `limits --reset` 으로 지울 수 있다).
- 슬롯까지 `max_wait`(60초)보다 오래 기다려야 하면 기다리지 않고
  `RateLimitedError(reason='budget')`.
- 정책에 `max_per_hour` 가 있으면 슬롯을 1시간 남겨 시간당 상한도 본다 (없으면 60초만 남긴다). 분당 상한으로는 막지 못하는 수십 분에 걸친 누적
  때문에 봇 차단을 받은 소스(스카이스캐너)용이다. 넘으면 대개 `max_wait` 를 넘어 `budget` 으로 끝나고, 문구가 '시간당' 이다.
- 정책에 `max_cooldown` 이 있으면 쿨다운이 끝나고 `max_cooldown` 안에 다시 막힐 때마다 쿨다운을 두 배로 늘린다 (상태의 `block_streak`). 차단 중에는
  쿨다운이 끝날 때마다 첫 요청이 다시 막히므로 그런 요청을 줄인다. 쿨다운 중에 들어온 차단(다른 프로세스가 함께 받은 것)은 늘리지 않는다.
- 429 응답을 받으면 `on_rate_limited` 가 쿨다운을 걸고 곧바로
  `RateLimitedError(reason='cooldown')` 를 던진다. 쿨다운(최소 15분)이 재시도 대기 상한(60초)보다
  길어 tenacity 재시도는 어차피 로컬에서 거절되기 때문이다. `RateLimitedError` 는
  `HTTPStatusError` 가 아니므로 재시도 대상이 아니다. 그래서 `handle_errors` 까지 오는 429 `HTTPStatusError` 는 제한을 끈
  정책(`enabled=False`, 라이브러리 사용)뿐이고 쿨다운이 걸리지 않았으므로 hint 에 쿨다운을 말하지 않는다.
- 연결 방식: `BaseHTTPClient.rate_limit_hooks()` / `async_rate_limit_hooks()` 를 httpx
  클라이언트의 `event_hooks` 로 넘긴다. 요청 전 훅이 슬롯을 기다리고, 응답 훅이 429 를 처리한다.
- 브라우저가 첫 화면을 열 때 스스로 보내는 사이트 API 요청과 CORS 사전 요청은 이 훅을 거치지 않는다. transport 가 기다리지 않고 지금 시각으로
  `RateLimiter.record(n)` 에 남겨 이어지는 요청의 간격·분당·시간당 상한 계산에 넣는다 ([브라우저 요청](#브라우저-요청)). 소스마다 끌 수 있다 (스카이스캐너는 끈다).

소스별 기본 정책 값은 각 client 의 `default_rate_limit` ClassVar 에 있다. 사용자에게 보여 주는 정책 표는
[공통 사용 규칙](../common.md#요청-제한과-limits)에 있으니, 정책을 바꾸면 그 표도 함께 고친다.
