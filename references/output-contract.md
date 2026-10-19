# Output Contract

baenggipyo 는 LLM 에이전트가 셸로 호출하는 도구라, CLI 의 출력 형식이 곧 공개 API 다.
여기 적힌 규칙을 바꾸는 변경은 `docs/common.md`와 해당 소스 CLI 문서의 출력 규칙·사용 지침도 함께 고친다.
구현은 `cli/output.py`, `cli/app.py` 의 `run()`, `core/flight.py`.

## stdout / stderr

아래 JSON 계약은 조회 결과와 에러에 적용한다. `--help`·자동완성·`guide`(LLM 가이드 `docs/llm.md`)·`--version`은 CLI 텍스트를 출력하고, `--verbose`는 stderr에 진행 로그를 추가한다.

- **결과는 stdout 에 JSON 하나.** `emit()` 으로만 쓴다. `print` 나 로그를 stdout 에 쓰지 않는다.
- **에러는 stderr 에 `{"error": {...}}` JSON 하나.** `emit_error()` 로만 쓴다. 사용법 오류
  (없는 옵션, 잘못된 값, 인자 누락)도 마찬가지다. 이를 위해 `run()` 이 Typer 를
  `standalone_mode=False` 로 돌려 Click 예외를 직접 잡는다.
- 로그(`--verbose`)는 `logging` 으로 stderr 에 나간다. 기본 레벨은 ERROR. 요청 한 줄 로그는 요청 제한 훅(`rate_limit_hooks`)이 쿼리를 떼고
  경로의 세션 ID 를 자리표시자로 바꾼 주소로 남긴다 (`baenggipyo.core.http`). httpx 자체 요청 로그는 주소를 그대로 찍어 끈다 (WARNING).
- JSON 은 터미널이면 들여쓰기, 파이프면 한 줄. `--pretty/--compact` 로 강제한다.
  `ensure_ascii=False` 로 한글을 그대로 쓴다.
- `--help` 도 같은 기준이다. 터미널이면 Rich 상자로, 파이프면 Click 평문으로 쓴다 (`cli/output.py` 의 `HELP_MARKUP_MODE` 를 최상위 Typer 의
  `rich_markup_mode` 로 넘긴다). 파이프의 Rich 상자는 80칸 안에 테두리·여백을 넣느라 같은 도움말이 두 배 넘게 길고 설명을 `…` 로 잘랐다 (2026-10-15).

## 에러

```json
{"error": {"code": "FARE_UNAVAILABLE", "message": "...", "hint": "...", "source": "naver", "itinerary_id": "..."}}
```

- `code` 는 기계가 읽는 값, `message` 는 사람이 읽는 설명, `hint` 는 **다음에 할 행동**이다.
  LLM 이 hint 를 보고 재시도·옵션 수정·소스 변경을 고르므로, 새 에러에는 구체적인 hint 를 단다.
- 애플리케이션 에러는 `core.errors.BaenggipyoError` (또는 서브클래스)로 던진다. 추가 필드는 `details` 에
  넣으면 에러 JSON 에 평탄하게 실린다.
- 응답 형식이 어긋난 것(이벤트 없는 SSE 등)은 client 가 `ValueError` 로 던져 위 표대로 `PARSE_ERROR` 가 된다. client 는 응답 검증만
  하고 `BaenggipyoError` 변환은 service 가 하므로([계층](architecture.md#계층)) 규칙 위반이 아니다. 앞 응답의 값으로 만드는 요청 모델은
  `built_from_response()` 안에서 만들어, 검증이 실패해도 `INVALID_ARGUMENT` 가 아닌 `PARSE_ERROR` 로 낸다.
- CLI 명령은 본문을 `with handle_errors(SOURCE):` 로 감싼다. 예외 → 코드 매핑:

| 예외 | code | exit |
| --- | --- | --- |
| `BaenggipyoError` | 예외의 `code` | 1 (`INVALID_ARGUMENT` 는 2) |
| pydantic `ValidationError` — 요청 모델(`RequestModel`) | `INVALID_ARGUMENT` | 2 |
| pydantic `ValidationError` — 응답 모델·공통 출력 모델 (응답 형식 변경) | `PARSE_ERROR` | 1 |
| `HTTPStatusError` 404 / 429 / 그 밖 | `NOT_FOUND` / `RATE_LIMITED` / `UPSTREAM_ERROR` | 1 |
| 그 밖의 `HTTPError` | `NETWORK_ERROR` | 1 |
| `UnicodeEncodeError` (결과를 쓰는 출력 인코딩이 UTF-8 이 아님. PYTHONUTF8 없는 Windows 파이프의 cp949) | `INTERNAL_ERROR` (hint: `PYTHONUTF8=1`) | 1 |
| 그 밖의 `ValueError` (JSON 아닌 응답의 `JSONDecodeError`, 이벤트 없는 SSE 등), `LookupError` (응답이 예상과 달라도, 내부 버그여도 나서 가를 수 없다. hint 에 `--verbose` 로 다시 실행하라고 덧붙인다) | `PARSE_ERROR` | 1 |
| 그 밖의 모든 예외 | `INTERNAL_ERROR` | 1 |
| Click `UsageError` (`run()` 에서) | `INVALID_ARGUMENT` | 2 |
| 그 밖의 Click `ClickException` (`run()` 에서, 파일 인자 오류 등. 지금은 나오지 않음) | `CLI_ERROR` | 예외의 `exit_code` |
| `KeyboardInterrupt` (Typer 가 `Exit(130)` 으로 바꾼 것), Click `Abort` (`run()` 에서) | `INTERRUPTED` | 130 (`Abort` 는 1) |

- 쓰이는 코드: `INVALID_ARGUMENT`, `AIRPORT_NOT_FOUND`, `AIRPORT_AMBIGUOUS`, `ROUTE_NOT_SUPPORTED`, `FARE_UNAVAILABLE`, `NOT_FOUND`,
  `RATE_LIMITED`, `UPSTREAM_ERROR`, `NETWORK_ERROR`, `PARSE_ERROR`, `INTERNAL_ERROR`, `INTERRUPTED`, `CLI_ERROR`.
  새 코드를 만들면 공통 사용 문서의 exit code 표와 오류 대응에 추가한다.
- 봇 차단(스카이스캐너 403 캡차)은 새 코드를 만들지 않고 `RATE_LIMITED` (`reason: blocked`)로 낸다. 할 일이 429 와 같기
  때문이다 (기다리거나 다른 소스). 소스 client 의 응답 훅이 차단을 알아보고 쿨다운을 걸어, 쿨다운 동안은 요청을 보내지 않는다.
- `ROUTE_NOT_SUPPORTED` 는 소스가 그 노선을 다루지 않을 때다 (네이버의 국내선 다구간, 국내·국제가 섞인 노선). hint 로 다룰 수 있는 다른 소스를 알린다.
- 입력 검증 실패는 항상 exit 2.
- 요청 모델 검증 실패의 message 는 검증 함수가 던진 문구 그대로다 (pydantic 이 붙이는 `Value error, ` 를 뗀다).
  hint 는 사용법 오류와 같은 `'<명령> --help' 로 사용법을 확인하세요` 다.
- 에러 JSON 의 `source` 는 기본으로 명령의 소스지만, `BaenggipyoError.details['source']` 가 있으면 그 값이 이긴다.

## 항공권 출력

- 항공권·가격을 내보내는 명령은 `emit(..., notice=True)` 로 맨 앞에 `notice`(가격은 조회 시점 기준, 예약은 원
  사이트에서)를 붙인다. 모호한 공항 안내, 검색이 끝나기 전에 멈춘 안내, 조건에 맞는 항공권이 `limit` 보다 많아 일부만
  돌려준다는 안내는 `notes` 로 그 아래에 붙이고, 없으면 키를 뺀다.
- 리스트를 그대로 내보내면 `notice` 를 붙이기 위해 `{"items": [...]}` 로 감싼다.

### 값이 없을 때

| 상태 | 표현 | 예 |
| --- | --- | --- |
| 해당 없음 (그 항공권·조회에 원래 없는 항목) | **키를 뺀다** | 코드셰어가 아닌 편의 `operating_airline`, 편도 달력의 `return_date` |
| 모름 (해당은 되지만 소스가 알려 주지 않음) | `null` | 기종을 알려 주지 않은 편의 `aircraft`, 수하물 정보가 없는 운임의 `baggage_included` |
| 알려 줬는데 비어 있음 | `[]` | 직항의 `stopover_airports` |

- service 에서 "모름"과 "없음"을 섞지 않는다. 수하물 정보가 없으면 `baggage_included=None` 이지 `False` 가 아니다.
- 키를 빼는 필드는 출력 모델의 `not_applicable_fields()` + `@model_serializer` 로 뺀다 (jibsa `Listing` 과 같은 방식).
- 예외: `flight --offer-urls N` 으로 링크를 뺀 판매처 운임의 `url` 도 키를 뺀다 (`Offer.url_omitted`). 모르는 것이 아니라 빼 달라고 한 것이라 `null` 로
  두지 않는다.

### 요약 (`--brief`)

`search flights --brief` 는 모양을 바꾸지 않고 키만 뺀다. 그래서 요약을 읽는 쪽도 전체 출력과 같은 경로(`journeys[].segments[].flight_number`)로 읽는다.
`emit(..., brief=True)` 가 직렬화 context 에 `BRIEF_CONTEXT_KEY` 를 넣으면, 각 출력 모델이 `brief_omitted_fields()` 의 필드를 뺀다. 빼는 것은 같은
결과의 다른 곳에 있는 값과 항공권을 고르는 데 필요 없는 값뿐이다. 값이 없을 때의 규칙(`null`·`[]`)은 요약에서도 그대로다.

| 모델 | 요약에서 빼는 필드 | 대신 볼 곳 |
| --- | --- | --- |
| `Itinerary` | `source`, `url`, `trip_type`, `cabin` | 결과의 `search`(`search.url`), 항공권 링크는 `flight <id>` |
| `Segment` | `airline_code`(편명을 알 때만), `operating_airline_code`(운항 항공사 이름을 알 때만), 공항·시각, `duration_minutes`, `aircraft` | 편명 앞 코드, 운항 항공사 이름, 여정 공항·시각과 `stopover_airports` (직항이면 여정과 똑같다) |
| `NaverDomesticItinerary` | 위에 더해 `direction` | `legs[].direction` |

### 금액·시각·코드

- 금액 필드는 전부 **원(KRW) 정수**이고 이름이 `_krw` 로 끝난다. 다른 통화로 오는 소스는 요청에서 KRW 를 지정하고,
  지정할 수 없으면 환산하지 않고 `notes` 로 알린다 (환율을 추측하지 않는다).
- 총액(`total_price_krw`, 모든 승객·세금·유류할증료 포함)과 1인 요금(`price_per_adult_krw`)을 이름으로 구분한다.
  소스가 둘 중 하나만 주면 다른 하나를 나눗셈·곱셈으로 만들지 않고 `null` 로 둔다 (아동·유아 요금이 다르다).
- 시각은 **각 공항의 현지 시각** `YYYY-MM-DDTHH:MM` 이다. 시간대 환산을 하지 않는다. 날짜만 있는 값은 `YYYY-MM-DD`.
- 소요 시간은 분 정수(`duration_minutes`). 출발·도착 시각의 차로 계산하지 않는다 (시간대가 다르다). 여정의
  소요 시간은 경유 대기를 포함한다.
- 공항·도시는 IATA 코드(`ICN`, 도시 `TYO`), 항공사는 IATA 2자리 코드(`KE`)와 이름을 함께 준다.

### 공통 출력 모델

모델은 `core/flight.py` 에 있고 필드 의미는 각 필드의 `description` 이 기준이다. 모두 `FlightModel` 을 상속해
`not_applicable_fields()` 로 해당 없는 키를 뺀다. 소스에만 있는 필드는 소스 서브클래스에 둔다 (네이버 `NaverItinerary` 의
`card_discount_*`, `NaverOffer` 의 `payment_condition`, 스카이스캐너 `SkyscannerItinerary` 의 `self_transfer`).

| 모델 | 쓰는 명령 | 필드 | 키를 빼는 경우 |
| --- | --- | --- | --- |
| `Airport` | `search airports` | `code`, `type`(`공항`·`도시`), `name`, `city`, `country`, `airports`, `domestic` | 공항의 `airports` |
| `SearchLeg` | `search flights` 의 `search.legs[]` | `origin`, `destination`, `date` | — |
| `Segment` | (여정 안) | `airline_code`, `airline`, `flight_number`, `operating_airline_code`, `operating_airline`, `departure_airport`, `arrival_airport`, `departure_at`, `arrival_at`, `duration_minutes`, `aircraft` | 공동운항이 아니면 `operating_*` |
| `Journey` | (항공권 안) | `departure_airport`, `arrival_airport`, `departure_at`, `arrival_at`, `duration_minutes`(경유 대기 포함), `stops`, `stopover_airports`, `segments` | — |
| `Itinerary` | `search flights` | `source`, `id`, `url`, `trip_type`, `cabin`, `journeys`, `total_price_krw`, `price_per_adult_krw`, `offer_count`, `cheapest_seller`, `baggage_included` | — |
| `Offer` | `flight` 의 `offers[]` | `seller`, `seller_type`, `total_price_krw`, `price_per_adult_krw`, `fare_name`, `baggage_included`, `url` | `--offer-urls` 로 링크를 뺀 운임의 `url` |
| `CalendarDay` | `calendar` 의 `days[]` | `date`, `return_date`, `min_price_krw`, `direct` | 편도의 `return_date` |
| `Destination` | `explore` 의 `destinations[]` | `code`, `city`, `country`, `country_code`, `min_price_krw`, `direct`, `departure_date`, `return_date` | 편도의 `return_date` |

- **대표 가격**(`total_price_krw`, `price_per_adult_krw`)은 결제수단 조건이 없는 운임의 최저가다. 특정 카드·결제수단으로만 받는 가격은 대표 가격으로
  쓰지 않고 소스 필드로 따로 준다 (2026-10-11 결정, 네이버).
- 총액과 1인 요금 중 소스가 하나만 주면 다른 하나를 나눗셈으로 만들지 않고 `null` 로 둔다. 승객 유형별 1인 운임을 모두 주면 인원을 곱해 더한 합은 쓴다.
- `CalendarDay`·`Destination` 의 가격은 추정가라 `notes` 로 알린다.
- 명령 결과를 감싸는 모델(`NaverFlightSearchResult` 등)은 각 service 모듈에 있고, `notes` 는 `Field(exclude=True)` 로 두어 `emit(..., notes=...)` 로 낸다.
  두 소스가 똑같은 모양으로 내는 `search airports` 결과(`AirportSearchResult`)와 `search flights` 의 `search`(`SearchSummary`)는 `core/flight.py` 에 하나만
  두고, 소스에만 있는 필드는 서브클래스로 붙인다 (스카이스캐너 `search.total_count`).

### 내보내지 않는 정보

baenggipyo 는 **찾기까지만** 한다. 예약을 대신하게 되는 정보와 기능은 넣지 않는다.

- 원본 응답 통째, 세션 토큰·쿠키·추적 ID, 판매처 내부 운임 코드는 내보내지 않는다. `url` 은 원 사이트가 주는 링크를 쓰되,
  링크에 세션 토큰이 들어 있으면 토큰 없이도 열리는지 조사한 뒤 정한다.
- 예외: 판매처 예약 링크(`offers[].url`)에 든 검색·견적 ID 는 원 사이트가 그 예약을 판매처로 이어 주는 데 쓰는 값이라 링크째 낸다. 네이버
  `tivan.naver.com` 링크는 경로 전체가 불투명한 값이라 뗄 토큰이 없고, 스카이스캐너 딥링크의 `fps_session_id`·`request_id`·`q_ids` 는 그 검색을
  가리킬 뿐 로그인·쿠키 값이 아니다 (2026-10-11 확인, 소스 개발 가이드). 로그인·쿠키처럼 사용자를 가리키는 값이 링크에 들어 있으면 내보내지 않는다.
- 승객 이름·여권·결제 정보는 받지도 내보내지도 않는다.
- 응답 모델(`api/response/`)에는 이런 필드가 있어도 되지만 공통 출력 모델로 옮기지 않는다.

### 매진·만료 운임

결과는 항상 실시간 조회다. 캐시를 두지 않는다. 소스별 판매 가능 조건은 API 조사 뒤 이 표에 적는다.

| 소스 | 판매 가능 조건 | 목록 | 상세 |
| --- | --- | --- | --- |
| 네이버 | 검색 결과의 운임 묶음(`fareMappings`)에 있음. 운임 상태값은 없다 | 응답에 있는 것만 낸다 | 다시 검색해도 없으면 `FareUnavailableError` (`reason`: `SEARCH_EXPIRED`·`NOT_IN_RESULTS`·`NO_ITINERARY`). 다시 묻는 검색이 시간 상한에 끊겨 못 찾았으면 매진으로 단정하지 않고 `SearchIncompleteError` (`UPSTREAM_ERROR`, `reason`: `SEARCH_INCOMPLETE`) |
| 스카이스캐너 | 검색 결과(`itineraries.results`)에 있음. 판매처 운임은 가격 없음 표시(`unpricedType`)가 없는 것만 | 응답에 있는 것 중 가격을 준 판매처 운임이 하나라도 있는 것만 낸다 | `flight` 가 다시 검색해 끝났는데 없거나 가격을 준 판매처 운임이 없으면 `FareUnavailableError` (`reason`: `NOT_IN_RESULTS`). 검색이 시간 상한 안에 끝나지 않아 못 찾았으면 매진으로 단정하지 않고 `SearchIncompleteError` (`UPSTREAM_ERROR`, `reason`: `SEARCH_INCOMPLETE`), 폴링이 요청 제한에 걸려 멈춰 못 찾았으면 `RateLimitedError`, 응답에 세션 ID 나 진행 상태가 없어 멈춰 못 찾았으면 다시 불러도 같아 `PARSE_ERROR` |

검색 세션이 만료돼 ID 로 상세를 볼 수 없으면 `FareUnavailableError(reason='SEARCH_EXPIRED')` 로 내고, hint 로 다시
검색하게 한다.

## 개수 상한

`--limit`의 최대값은 CLI 정책이며 서버의 요청당 최대 개수와 구분한다.

- 소스가 결과를 페이지로 주면 `--limit` 은 조회 할당량이다. 실제로 받은 원본 수로 세고, 받은 뒤 중복·판매 불가·조건
  불일치로 뺀 만큼 다시 채우지 않는다.
- 소스가 폴링이 끝난 결과를 한 번에 주면 정렬한 뒤 앞에서 `--limit` 개를 자른다. 정렬은 소스가 준 순서를 쓰고,
  다시 정렬할 때는 그 기준을 `notes` 로 알린다.
- `returned`는 실제 반환 수이며 요청한 `limit`과 같다는 보장이 없다. 전체 결과 수·폴링 미완료·제외 사유는 `notes`로 설명한다.

조회 방식별 예산과 한 소스에만 있는 예외는 그 소스의 provider 가이드(`references/providers/`)에, 명령별 기본값·최대값은
사용자 문서의 해당 명령 절에 적는다. 상한을 올리면 요청 수가 늘 수 있으므로 요청 제한 정책과 함께 검토한다.
