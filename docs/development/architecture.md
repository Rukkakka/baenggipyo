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
         └─ core/http.py BaseHTTPClient   httpx 클라이언트 + 요청 제한 훅
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

- 공통 필드: `url_schema`, `verify`, `proxy`, `timeout`, `user_agent`, `rate_limit`.
  `extra='forbid'` 라 오타 난 설정은 생성 시점에 실패한다.
- 서브클래스는 `_client` / `_async_client` 를 `@cached_property` 로 만든다. `close()` 가
  `self.__dict__.pop('_client', None)` 으로 꺼내 닫으므로 이름이 정확히 같아야 한다.
  (`@cached_property` 와 `@abstractmethod` 는 함께 쓸 수 없어 베이스에서는 일반 property 로 선언한다.)
- `aclose()` 는 async 와 sync 를 모두 닫는다. `close()` 는 sync 를 닫고, `with` 안에서 async 메서드를 써 async 클라이언트도 있으면 실행 중인
  이벤트 루프에 그 닫기를 맡긴다 (sync 함수라 기다릴 수 없다. 루프가 이미 끝났으면 참조만 놓는다). 닫은 뒤 다시 접근하면
  새 클라이언트를 만들므로 인스턴스를 재사용할 수 있다.
- `with` / `async with` 를 지원한다. CLI 와 service 호출부는 항상 컨텍스트 매니저로 쓴다.

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
  넣어 늘 온전한 한 벌을 읽으므로, 상태 디렉터리에 쓸 권한이 없어도 `limits` 는 된다. 쓰기는 `write_json_atomic` 하나로 하고(판매처 이름표도 같다),
  임시 파일 이름에 프로세스 ID 를 넣어 잠금 없는 쓰기끼리 임시 파일을 덮어쓰지 않는다.
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

소스별 기본 정책 값은 각 client 의 `default_rate_limit` ClassVar 에 있다. 사용자에게 보여 주는 정책 표는
[공통 사용 규칙](../common.md#요청-제한과-limits)에 있으니, 정책을 바꾸면 그 표도 함께 고친다.
