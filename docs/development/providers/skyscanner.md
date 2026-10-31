# 스카이스캐너 개발 가이드

[CLI 사용법](../../skyscanner.md) · [개발 환경·검증](../README.md) · [공통 아키텍처](../architecture.md) · [provider 작성 규약](../provider-conventions.md) · [출력 계약](../output-contract.md) · [코드 형식](../code-style.md)

공통 규약에 없는, 스카이스캐너(skyscanner.co.kr)에만 있는 구현과 예외를 적는다.

> **상태: 구현 완료 (2026-10-11).** 홈 화면(`banana` 마이크로사이트) JS 번들을 읽고 실제로 호출해 확인했다. 모두 로그인·API 키 없이
> 된다. 결과 페이지 URL 은 번들의 생성 규칙과 한국어 번역(`ktxtURLTransport` = transport)으로 만들었고, 다구간·왕복 URL 모두 브라우저에서
> 검색 결과가 열리는 것을 확인했다. 사이트 HTML 을 직접 받으면 봇 차단 위험이 커서 URL 확인은 브라우저로 한다.

## 명령과 요청 흐름

| CLI 명령 | 주요 경로 | service 모듈 |
| --- | --- | --- |
| `search airports` | `GET /g/autosuggest-search/api/v1/search-flight/KR/ko-KR/{검색어}` 1회 | `airport` |
| `search flights` (편도·왕복·다구간) | 공항 해석(입력마다 자동완성 1회) → `POST /g/radar/api/v2/web-unified-search/` → 완료까지 `GET …/{sessionId}` 폴링 | `airport` → `flight` |
| `flight` | 같은 조건으로 다시 검색(POST + 폴링)해 그 항공권의 `pricingOptions` 를 쓴다 (따로 부르는 상세 API 없음) | `flight` |
| `calendar` | 공항 해석 2회 → `POST /g/search-intent/v1/pricecalendar` 1회 (편도 날짜별) | `airport` → `calendar` |
| `explore` | 공항 해석 1회 → 검색 API 에 목적지 `everywhere`, `targetGeoLevel: city` 로 1회 (결과가 바로 완료) | `airport` → `explore` |

## 소스 고유 구현

### 공식 API 를 쓰지 않는 이유

스카이스캐너는 파트너용 공식 API 를 제공하지만 API 키가 필요하다. baenggipyo 는 계정·API 키를 받지 않으므로
([README 원칙](../../../README.md#원칙-로그인-없는-기능만)) 웹 페이지가 로그인 없이 부르는 엔드포인트를 쓴다.

### 봇 차단

- 홈 페이지가 PerimeterX 스크립트(`/rf8vapwA/init.js`)와 `_pxhd` 쿠키를 준다. baenggipyo 는 쿠키를 받거나 보내지 않는다.
- 차단 응답은 `403 {"reason": "blocked", "redirect_to": "/sttc/px/captcha-v2/index.html?url=…&uuid=…"}` 다. 몇 시간씩 이어지기도 하고, 거듭 막힐수록
  길어지는 것으로 보인다.
- 차단은 우회하지 않는다. `SkyscannerClient` 의 응답 훅(`_check_blocked`)이 403 본문의 `"blocked"`·`captcha` 로 알아보고 요청 제한 쿨다운(30분, 거듭되면 두 배씩 최대 4시간)을
  건 뒤 `RateLimitedError(reason='blocked')` 를 낸다. 쿨다운 동안은 다른 프로세스도 요청을 보내지 않는다. 에러 코드는 `RATE_LIMITED` 다
  ([에러 코드](../output-contract.md#에러)). 차단 중에 확인할 때는 쿨다운이 끝난 뒤 가벼운 요청 1회만 보낸다.
- 같은 컴퓨터의 브라우저로는 열리는데 baenggipyo 만 막히기도 한다. IP 만이 아니라 클라이언트(검사 스크립트 실행·`_px*` 쿠키·TLS 지문)를 보고 막는 것이다.
  브라우저 쿠키를 옮겨 넣거나 지문을 흉내 내면 통과할 수 있겠지만 그것이 우회라 CLI 는 하지 않는다. CLI 는 기다리는 것 말고는 방법이 없다.
- 검증용인 매일 API 감시만 예외로, GitHub 러너(데이터센터 IP)에서 막히는 날을 줄이려고 스카이스캐너 요청의 TLS 지문을 크롬처럼 바꾼다
  (`scripts/monitor/disguise.py`, [매일 API 감시](../README.md#매일-api-감시)). 쿠키·캡차는 다루지 않고 설치되는 CLI 에는 넣지 않는다.
- 스카이스캐너 실측은 하루에 검색 한두 번으로 줄이고 나머지는 가짜 응답으로 확인한다.

### 헤더와 시장·통화

- `User-Agent`·`Accept-Language` 는 브라우저 값을 보낸다. 검색 API 는 `X-Skyscanner-ChannelId: website`, `X-Skyscanner-Market: KR`,
  `X-Skyscanner-Locale: ko-KR`, `X-Skyscanner-Currency: KRW`, `X-Skyscanner-ViewId: <uuid>` 로 불렀다. 이 값으로 금액이 KRW 로 온다.
- 달력 API 는 같은 값을 본문의 `headers` 객체(`xSkyscannerClient`, `xSkyscannerCurrency`, `xSkyscannerLocale`, `xSkyscannerMarket`)로 받는다.

### 장소 ID

ID 가 세 종류다. 명령 사이에서 섞이지 않게 한다.

| 이름 | 예 | 쓰는 곳 |
| --- | --- | --- |
| IATA 코드 | `ICN`, `NRT` | 사람이 보는 코드, 날짜별 달력(`…RelevantFlightSkyId`) |
| 스카이 ID (`PlaceId`·`flightPlaceId`) | 공항 `ICN`, 도시 `SELA`·`TYOA` (도시 IATA 뒤에 `A`) | 어디든지 결과(`skyCode`) |
| 엔티티 ID (`GeoId`·`entityId`) | 인천 `95673659`, 나리타 `128668889`, 서울 `27538638`, 도쿄 `27542089` | 검색·월별 달력 |

자동완성 결과 하나에 세 가지가 다 있으므로, `search airports` 결과의 IATA 코드를 받아 검색할 때 자동완성으로 엔티티 ID 를 다시 찾는다.

### 공항 검색 (자동완성)

`GET /g/autosuggest-search/api/v1/search-flight/KR/ko-KR/{검색어}?isDestination=…&enable_general_search_v2=true`, `Accept: application/json`,
`Platform: web`. 결과 배열의 항목마다 `PlaceId`, `PlaceName`, `IataCode`(도시만), `CityId`, `CityName`, `CountryId`·`CountryName`, `GeoId`,
`Tags`(`NEARBY_CITY` 등). '도쿄' → `TYOA`(도쿄), `NRT`, `HND` …

### 항공권 검색 (`web-unified-search`)

- `POST /g/radar/api/v2/web-unified-search/` 본문:
  `{"cabinClass": "ECONOMY", "childAges": [], "adults": 1, "legs": [{"legOrigin": {"@type": "entity", "entityId": "…"}, "legDestination": {…},
  "dates": {"@type": "date", "year": "2026", "month": "11", "day": "15"}}]}`. 왕복·다구간은 `legs` 를 늘린다. 근처 공항 포함은
  `options.includeOriginNearbyAirports`·`includeDestinationNearbyAirports`.
- 응답 `context.status` 가 `incomplete` 면 `context.sessionId` 로 `GET /g/radar/api/v2/web-unified-search/{sessionId}` 를 다시 부른다.
  사이트의 간격: 처음 4번 1초, 8번까지 2초, 12번까지 3초, 그 뒤 4초. 첫 요청 60초가 지나면 멈춘다. 실측: 성인 1명 편도는 첫 폴링(약 1초)에,
  성인 2명은 4번째(약 4초)에 `complete` (결과 184개, 마지막 응답 2.1MB).
  폴링 응답마다 `context` 에 `status`·`sessionId` 가 다시 온다 (김포↔광주 왕복 POST 1 + 폴링 3, 2026-10-11). 그래도 `sessionId` 가 빠지면 처음 받은 값으로
  계속 묻고, 완료는 `status: complete` 로만 판정한다 (`service/common.py` `run_search`).
  세션 ID 를 한 번도 받지 못했거나(빈 문자열 포함), 진행 중(`incomplete`)도 완료도 아닌 응답(`context`·`status` 가 빠졌거나 모르는 값)이 오면 더 묻지 못해
  미완료로 끝낸다. 시간 상한에 멈춘 것과 달리 다시 불러도 같으므로 `notes` 에 그 까닭을 적고, `flight` 는 `SEARCH_INCOMPLETE`(잠시 뒤 다시) 대신
  `PARSE_ERROR`(`reason`: `SESSION_ID_MISSING`·`SEARCH_STATUS_UNKNOWN`)로 낸다. 시간 상한으로 알리면 매일 API 감시도 고장이 아닌 확인 못 함으로 보아
  응답 형식이 바뀐 것을 놓친다. 폴링 요청 모델은 응답 값으로 만드는 것이라 `built_from_response` 안에서
  만들어, 값이 어긋나도 입력 오류(`INVALID_ARGUMENT`)가 아닌 `PARSE_ERROR` 로 낸다 (ICN→BCN POST 1 + 폴링 3 모두 세션 ID 가 있었다, 2026-10-11).
- 결과 `itineraries.results[]`:
  - `id`(예 `12409-2611150810--31158-0-14788-2611151025`), `price.raw`(KRW), `tags`(`cheapest`·`shortest` …), `isSelfTransfer`,
    `farePolicy`(변경·환불 가능 여부).
  - `legs[]`: `origin`·`destination`(IATA·엔티티 ID·이름·도시), `departure`·`arrival`(현지 시각 `YYYY-MM-DDTHH:MM:SS`), `durationInMinutes`,
    `stopCount`, `timeDeltaInDays`, `carriers.marketing[]`, `segments[]` — `flightNumber`, `marketingCarrier`·`operatingCarrier`(`displayCode`
    가 IATA 항공사 코드, 예 에어서울 `RS`; `alternateId` 는 스카이스캐너 내부 코드). 운항 항공사 코드가 둘 다 비면 판매·운항 항공사 이름이 둘 다 있고
    다를 때만 공동운항으로 보고 `operating_airline_code` 를 `null` 로 낸다 (`core.flight.Segment`). ICN→FUK 편도 96건의 구간 175개는 모두 운항 항공사
    코드가 있었다 (2026-10-19).
  - `pricingOptions[]`: 판매처마다 `agentIds`, `price.amount`, `price.updateStatus`(`current`), `items[].url`(예약 딥링크,
    `/transport_deeplink/4.0/KR/ko-KR/KRW/…`), `items[].bookingProposition`(`PBOOK`). 실측 항공권 184개 중 89개가 판매처 5곳 이상.
- 판매처 이름은 `itineraries.agents[]`(`id`, `name`, `isCarrier` = 항공사 직판 여부, `rating`)에 있다 (실측 49곳).
- **판매처별 가격이 검색 결과에 이미 들어 있어 `flight` 는 따로 부를 API 가 없다.** 대신 세션이 없으면 결과를 다시 볼 수 없다.
  `sessionId` 를 나중에 다시 GET 했을 때 언제까지 결과를 주는지는 구현할 때 확인한다.
- 딥링크(`/transport_deeplink/4.0/KR/ko-KR/KRW/{판매처}/{승객}/{여정}/`)의 쿼리에 `fps_session_id`·`request_id`·`q_ids`·`pqid`·`client_id`·
  `q_datetime_utc`·`ticket_price` 등이 들어 있다 (2026-10-11). 모두 그 검색·견적을 가리키는 값이고 로그인·쿠키 값은 없다. 판매처로 넘어가는 원 사이트
  링크라 `offers[].url` 로 그대로 낸다 ([내보내지 않는 정보](../output-contract.md#내보내지-않는-정보)의 `url` 예외). 이 값을 빼도 열리는지, 세션이 지난
  뒤에도 열리는지는 아직 확인하지 않았다. 사이트 HTML 을 직접 받으면 봇 차단 위험이 커서 브라우저로만 확인한다.

### 날짜별 최저가 달력

- `POST /g/search-intent/v1/pricecalendar` 본문 `{"headers": {…}, "originRelevantFlightSkyId": "ICN", "destinationRelevantFlightSkyId": "NRT"}`.
  응답 `flights.days[]` 에 오늘부터 366일의 `day`, `price`(원), `group`(`low`·`medium`·`high`). `priceForReturnFlight` 는 편도 조회에서 `null`.
  왕복 달력을 받는 방법은 구현할 때 확인한다.
- `POST /g/search-intent/v1/pricecalendar/month` 본문 `{"headers": {…}, "originId": <엔티티 ID>, "destinationId": <엔티티 ID>, "isDirect": false,
  "tripType": "TRIP_TYPE_ONEWAY" 또는 "TRIP_TYPE_RETURN"}` 은 달마다 최저가 하나(`flightPricesPerMonth[]`, `priceCategory`)를 준다.
- **실시간 가격이 아니다.** 11/15 달력 값 167,334원과 같은 날 검색 최저가 167,504원이 조금 달랐다. 출력에 추정가라고 `notes` 로 알린다.

### 어디든지

- 검색 API 에 `"legDestination": {"@type": "everywhere"}`, `"dates": {"@type": "month", "year": "2026", "month": "11"}`(또는 `{"@type": "anytime"}`)를
  보내면 `everywhereDestination` 이 바로 `complete` 로 온다 (폴링 없음). 응답 위쪽 `context.status` 와 `everywhereDestination.context.status` 가
  둘 다 `complete` 였다 (서울 출발 11월 도시 단위, 결과 102건, 2026-10-12 실측).
- 기본은 나라 단위다. 본문 최상위에 `"targetGeoLevel": "city"` 를 주면 도시 단위가 온다 (서울 출발 11월: 도시 100곳 안팎).
- 결과 `results[]` 중 `type: LOCATION` 만 쓴다. `ADVERT` 와 `inlineCreative`(관광청 광고)가 섞여 온다. 항목마다 `content.location`(`name`, `skyCode`
  예 `CJUA`, `type` `City`·`Nation`), `content.parentLocation`(나라: `name`, `skyCode` 는 ISO 2글자 `KR`)과 `flightQuotes.cheapest`·`direct`(`rawPrice`,
  `direct`, `price` 표시 문자열). 날짜와 출발 공항은 주지 않는다. 나라는 2026-10-20 실측에서 도시 100곳 모두 `location` 밖 `content.parentLocation` 에
  왔고 `location.parentLocation` 은 null 이었다 (예전에는 `location` 안에 와서 0.3.1 까지 `country` 가 늘 null 이었다). 두 곳을 다 읽는다.
  `skyCode` 는 IATA 가 아니고 규칙으로 바꿀 수도 없다 (`CJUA` 제주, `CYNT` 옌타이 YNT, `JHSG` 사가 HSG). 자동완성에 넣어도 그 도시가 나오지 않아
  (`CJUA` → 청주·청두) 출력에서는 `code` 를 null, 이 값을 `sky_code` 로 낸다.
- 지표 가격은 그 기간 어느 날의 최저가다. 인천 출발 11월 제주 26,928원은 달력의 11/9·11/23(26,913원) 같은 날의 값이었고, 11/15 는 달력 315,865원,
  검색 315,470원(직항 없이 경유)이었다 (2026-10-20 실측). 버그가 아니라 날짜를 주지 않는 값이라 `notes` 로 `calendar` 를 먼저 쓰게 안내한다.
  `--round-trip` 은 `calendar` 가 편도만 되어 그 달 날짜로 `search flights` 하게 안내한다.
  지표 가격이 없는 도시(`--direct` 면 직항 지표 가격이 없는 도시)는 싼 순으로 세울 수 없어 빼고 그 수를 `notes` 로 알린다.
- 가격은 달력처럼 지표 가격(indicative)이다.

### 구현 메모 (2026-10-11 실측)

- **장소**: 자동완성 후보에서 `PlaceId == CityId` 면 도시, `PlaceId == CountryId` 면 나라(쓰지 않음), 나머지는 공항이다. 도시 코드는 `IataCode`(SEL),
  스카이 ID 는 `PlaceId`(SELA)다. 해석 규칙은 네이버와 같은 `core.flight.pick_place` 다 (코드는 공항 먼저, 이름은 도시 먼저).
  `IataCode` 가 빈 도시는 `search airports` 의 `code` 를 null 로 낸다 (`_iata_code`). 스카이 ID 를 내면 4글자라 그대로 검색에 넣었을 때
  `check_place_inputs` 에 걸린다. 그런 도시를 실측으로 본 적은 아직 없고 응답 모델상 빈 값이 올 수 있어 막아 둔 것이다.
  - **예외: `CityId` 가 빈 도시 후보가 있다** (2026-10-12 실측). '부산' 자동완성의 도시 후보는 `PlaceId: PUSA`, `IataCode: PUS`, `CityId: ""`,
    `Tags: ["GENERAL_SEARCH_V2"]` 이고 가까운 다른 공항(`AirportInformation`, 울산)을 함께 준다. 위 규칙만으로는 공항(code `PUSA`)이 됐다.
    같은 응답의 `PUS` 공항 후보는 `CityId: PUSA` 이고, 어디든지 결과도 부산을 `PUSA` 도시로 준다. 도시 후보는 `GeoId == GeoContainerId` 다
    (`PUSA`·`OSAA`·`BUCH`). 공항은 둘이 다르다 (`PUS` 128667080 / 27546072). 그래서 `CityId` 가 비면 이것으로 도시를 가린다 (`_is_city`).
  - **같은 엔티티가 거듭 온다.** '서울' 은 보통 도시 후보(`CityId: SELA`) 뒤에 `CityId` 가 빈 `GENERAL_SEARCH_V2` 후보로 `SELA` 가 두 번 더 온다 (같은 `GeoId`).
    빈 `CityId` 를 도시로 보면 서울이 세 곳(`AIRPORT_AMBIGUOUS`)이 되므로 같은 `GeoId` 는 처음 후보만 쓴다 (`_autosuggest`).
- **도시로 검색**하면 그 도시 공항이 모두 나온다. 인천↔도쿄(`TYO`, 엔티티 27542089) 왕복 2,813건에 나리타·하네다가 섞여 왔다 (가는 편 도착·오는 편 출발이
  NRT·NRT 2,175, HND·NRT 342, NRT·HND 260, HND·HND 36, 2026-10-12). 가격순 앞 50건은 모두 나리타였다.
- **날짜별 달력**은 스카이 ID 를 받는다. 도시를 IATA(`SEL`)로 주면 500 `Application error processing RPC`, `SELA` 는 200 이다.
- **소아·유아**: 나이로 받으므로 소아 8세, 유아 1세로 보낸다 (`CHILD_AGE`, `INFANT_AGE`).
- **항공권 ID**: 스카이스캐너 항공권 ID 는 장소·시각·항공사로 만든 값이라 다시 검색해도 같다. 왕복·다구간은 여정 ID 를 `|` 로 잇는다 (김포↔광주 왕복 244건
  모두). 따옴표 없이 셸에 넣으면 파이프가 되므로 baenggipyo ID 에는 `_` 로 바꿔 담고 `parse_flight_id` 가 되돌린다 (예전 `|` ID 도 받는다). 여정 하나의
  판매 항공사가 여럿이면 항공사 코드를 `,` 로 잇는다 (`…--31347,-32514-1-…`, ICN→LHR 편도 202건 중 79건, 부산↔후쿠오카 왕복 999건 중 229건). 쉼표는
  셸에서 그대로라 바꾸지 않고 `FLIGHT_ID_PATTERN` 이 받는다 (2026-10-11). `flight_id()` 는
  검색 조건(엔티티 ID·스카이 ID·날짜·승객·좌석)을 함께 담고, `flight` 는 매번 같은 조건으로 다시 검색해 그 ID 를 찾는다. 1시간 지난 `sessionId` 도
  결과를 줬지만 세션 ID 가 200자쯤이라 ID 에 넣지 않았다.
- **어디든지 왕복**은 두 번째 구간(`everywhere` → 출발지, 같은 달)을 넣으면 된다. 도시 결과에 `parentLocation`(나라)이 빠지면 `country` 는 null.
  `type: City` 만 쓰고 광고(`ADVERT`)와 나라 항목(관광청 광고 등)은 뺀다.
- **다구간**: `legs` 를 늘리면 된다. ICN→NRT 11/15 + KIX→ICN 11/20 은 1,860건, 25초였고 최저가 조합(TW241+TW318)이 네이버 다구간 1위와
  같았다 (2026-10-11). 결과 페이지는 `/transport/d/{출발 스카이 ID}/{YYYY-MM-DD}/{도착 스카이 ID}/…/` 이고 `rtn` 등은 붙이지 않는다.
- **편명**: `flightNumber` 는 숫자만이라 `marketingCarrier.displayCode`(IATA)를 앞에 붙인다. `alternateId`(`S~`)는 내부 코드다.
- **구간 없는 항공권**: `legs` 나 `segments` 가 빈 항공권은 보여 줄 수 없어 `search flights` 가 빼고 그 수를 `notes` 로 알린다. `flight` 도 결과에 없는
  것(`NOT_IN_RESULTS`)으로 낸다. 실측 244·202·999건에는 없었다 (2026-10-11).
- **판매처 링크**: `pricingOptions[].items[].url` 은 상대 경로라 사이트 주소를 붙인다 (4,693개 모두. 절대 주소로 오면 그대로 쓴다, `_deeplink`). 하나에
  1~2KB 라 `flight --limit`(기본 10)으로 자른다.
- **가격 없는 판매처 운임**: `pricingOptions[]` 중 `unpricedType`(`itinerary_not_returned`·`partner_not_selected`)이 있는 운임은 `price.amount` 가 0 이다
  (ICN→LHR 1,312개 중 31개, 부산↔후쿠오카 3,375개 중 175개 모두. 대표 가격으로 쓰인 것은 없었다, 2026-10-11). 살 수 있는 가격이 아니라 `flight` 의
  `offers` 와 `seller_count` 에서 빼고 뺀 수를 `notes` 로 알린다 (`_priced_options`). 전에는 `버진애틀랜틱항공 0원` 이 맨 앞에 나왔다.
  판매처 운임이 **모두** 가격 없음인 항공권은 살 수 있는 가격이 없어 `search flights` 가 빼고 그 수를 `notes` 로 알리며, `flight` 는 결과에 없는 것
  (`NOT_IN_RESULTS`)으로 낸다. ICN→BCN 203건에는 없었고(일부 운임만 가격 없음 36건) 대표 가격이 가격 없음 운임인 것도 없었다 (2026-10-11).
- **판매처 수**: `seller_count` 는 가격을 준 운임 수다. 한 판매처 운임은 판매처마다 하나라 판매처 수와 같다 (ICN→BCN 203건 모두, 2026-10-11).
  판매처 여러 곳에서 따로 예약하는 조합(아래)은 운임 하나로 센다.
- **대표 가격의 판매처**: `cheapest_seller` 는 `price.pricingOptionId` 와 운임 ID 가 같은 판매처 운임의 판매처다. 대표 가격에 운임 ID 가 없으면 어느
  운임인지 모르므로 `null` 이다 (ID 가 없는 운임과 짝지으면 다른 판매처를 낸다, `_cheapest_option`).
- **셀프 환승**: `isSelfTransfer: true` 는 표를 따로 사서 갈아타는 여정이라 연결편을 놓쳐도 보호받지 못하고 짐을 다시 부쳐야 할 수 있다. 출력에
  `self_transfer: true`(스카이스캐너 전용 필드, 아니면 키 없음)와 `notes` 경고로 낸다. ICN→BCN 203건은 모두 `false` 라 `true` 인 실제 응답은 아직
  보지 못했다 (2026-10-11). 보이면 노선과 수를 여기에 적는다.
- **따로 예약하는 조합**: `items` 가 여럿인 운임은 판매처 여러 곳에서 따로 예약한다. 부산↔후쿠오카 왕복 운임 3,375개 중 6개였고, `agentIds` 에 판매처가
  둘(`easr`, `tway`), `items[]` 마다 `agentId`·`price`·`segmentIds`·`url` 이 따로 있으며 단위 가격의 합이 운임 가격이었다 (2026-10-11). 한 줄로 내면 다른
  구간의 예약 링크가 빠지므로 `flight` 는 단위마다 한 줄(`fare_name`: `PUS→FUK만 (나머지는 … 에서 따로 예약)`)을 내고, 조합도 조합 합계로 한 판매처
  운임과 함께 싼 순으로 세운다. 한 판매처 운임 뒤에 따로 두면 조합이 대표 가격일 때 `--limit` 에 잘려 `offers` 에 대표 가격의 운임이 없었다
  (2026-10-13 리뷰). 대표 가격이 조합이면 `cheapest_seller` 는 판매처 이름을 ` + ` 로 잇는다. 인천↔도쿄 왕복 2,813건 중 35건에 조합이 있었고, 그
  응답으로 `--limit` 이 조합 줄을 가르지 않는 것을 확인했다 (2026-10-12).
- **검색 미완료**: `flight` 가 60초 안에 `complete` 를 받지 못했는데 그 항공권이 아직 없으면 매진으로 단정하지 않고 `UPSTREAM_ERROR`
  (`reason: SEARCH_INCOMPLETE`)로 낸다. `FARE_UNAVAILABLE` 의 hint(후보에서 빼라)를 따르면 아직 파는 항공권을 버리기 때문이다. 찾았으면 판매처가 더
  있을 수 있다고 `notes` 로 알린다.

## Action id 목록

경로 규칙대로 만든다 ([명명 규칙](../provider-conventions.md#action-id-명명)). 공통 접두어 `/g/` 는 버린다.

| 요청 | Action id |
| --- | --- |
| `GET /g/autosuggest-search/api/v1/search-flight/{시장}/{로캘}/{검색어}` | `autosuggest_search_search_flight_v1` |
| `POST /g/radar/api/v2/web-unified-search/` | `radar_web_unified_search_v2` |
| `GET /g/radar/api/v2/web-unified-search/{sessionId}` | `radar_web_unified_search_poll_v2` (경로 파라미터만 다른 같은 경로라 뜻으로 구분) |
| `POST /g/search-intent/v1/pricecalendar` | `search_intent_pricecalendar_v1` |
| `POST /g/search-intent/v1/pricecalendar/month` | `search_intent_pricecalendar_month_v1` (월별 최저가. 명령에서 쓰지 않아 구현하지 않았다) |

## 원본 금액 단위

KRW 를 요청하면 원으로 온다 (`price.raw` 는 `175500.0` 처럼 실수, 달력 `amount` 는 문자열 `"91768"`). 정수로 바꾼다.
**검색 가격은 모든 승객 합계다** (같은 항공편이 성인 1명 175,500원 → 2명 354,800원, 딥링크에 `passengers=2`). 1인 요금은 주지 않으므로
`price_per_adult_krw` 는 성인 1명만 찾을 때(합계와 같음)만 채우고 그 밖에는 `null` 이다 (나눗셈으로 만들지 않는다).

## 페이지네이션과 조건 처리

- 검색은 폴링이 끝난 결과를 한 번에 준다 (`filterStats.total` = 결과 수). 페이지가 없으므로 정렬 뒤 앞에서 `--limit` 개를 자른다
  ([개수 상한](../output-contract.md#개수-상한)).
- 직항·항공사·출발 시각대 조건을 받는 요청 필드를 번들에서 찾지 못했다. 그래서 받은 결과에서 직접 거른다
  ([서버 필터 없는 대체 조회](../provider-conventions.md#서버-필터-없는-대체-조회)): 직항은 모든 여정의 `stopCount == 0`, 항공사는 모든 구간의 판매
  항공사(`marketingCarrier.displayCode`)가 준 코드 안에 있을 때만 통과. 출발 시각대는 첫 여정(`--depart-time`)·둘째 여정(`--return-time`)의 현지
  출발 시각(`legs[].departure`)의 시가 시작 시 이상 끝 시 미만일 때만 통과 (경계는 네이버 칸에 맞춘 `core.flight.DEPART_HOUR_BOUNDARIES`). 값이 없으면 통과시키지 않는다. 거른 사실과 남은 수를 `notes` 로 알리고, 판정할
  값이 없어 뺀 항공권은 그 수를 따로 알린다 (실측 202·999건에는 `stopCount`·`displayCode` 가 빠진 것이 없었다, 2026-10-11).
- 정렬(가격·출발시각·소요시간)도 받은 결과를 baenggipyo 가 세운다. 결과가 이미 전부라 세운 뒤 잘라도 빠지는 항공권이 없다. 원본 값(`price.raw`,
  첫 여정 `departure`, 여정 `durationInMinutes` 합)으로 세운 뒤 앞의 `--limit` 개만 출력 모델로 바꾼다. 전부 바꾸면 8,484건에서 0.8초를 더 쓴다
  (응답 검증 0.9초와 비슷, 2026-10-11 측정).
- **응답이 매우 클 수 있다.** 폴링 응답마다 그때까지의 결과 전체가 온다. 김포↔제주 왕복은 8,359건 104MB 였다 (받기 8초, JSON 0.7초, 검증 1초).
  결과를 나누는 요청 옵션은 번들에서 찾지 못했다. 사이트 간격(1초)으로 폴링하면 같은 데이터를 여러 번 받으므로 간격을 2·3·5·8·12초로 늘렸다
  (`POLL_WAITS`), 첫 응답 뒤 60초가 지나면 받은 데까지 쓴다. 시간은 POST 응답을 받은 뒤부터 잰다. POST 앞에서 요청 제한 슬롯을 기다린 시간(최대
  60초)까지 세면 바로 앞 명령 때문에 폴링이 일찍 끝난다.
- **중간 폴링 응답도 전부 검증한다 (일부러).** 끝나지 않은 폴링 응답에서 쓰는 것은 `context` 뿐이지만, client 는 검증한 응답 모델을 돌려준다는
  계층 규약([요청 메서드](../provider-conventions.md#요청-메서드))을 지킨다. 8,000건 가상 응답(42MB)으로 재 보니 검증 1.1초, JSON 0.7초였다
  (2026-10-12). 큰 검색에서 폴링 한 번에 받기만 8초라 검증을 건너뛰어도 크게 줄지 않고, 건너뛰려면 client 가 검증하지 않은 응답을 돌려줘야 한다.
  메모리는 다르다: 파싱한 dict 154MB, 모델 233MB 라 앞 응답을 들고 다음 응답을 받으면 두 벌이 겹친다. 그래서 `run_search` 는 다음 폴링 전에 앞
  응답을 놓는다. 놓은 뒤 폴링이 로컬 요청 제한에 걸리면 받은 결과를 잃으므로, 놓기 전에 `RateLimiter.reserve_ahead()` 로 폴링 요청의 슬롯을 먼저
  잡아 두고, 잡지 못하면 받은 데까지 돌려준다 (시간당 30회 상한은 검색 도중에 닿기 쉽다). 미리 보기만 하고 놓은 뒤에 예약하면 그 사이 다른 프로세스가
  마지막 슬롯을 가져갈 수 있다 (2026-10-13 리뷰). 슬롯은 폴링 시간 상한까지 남은 시간 안의 것만 잡는다(`reserve_ahead(within=…)`). 상한 검사 뒤에
  슬롯 대기(최대 60초)가 붙으면 검색 한 번이 2분 가까이 걸린다 (2026-10-17 리뷰). 잡은 뒤에 다른 프로세스가 쿨다운을 걸거나 폴링이 봇 차단을 받으면
  여전히 에러로 끝난다. 폴링이 서버 오류(5xx·타임아웃)로 재시도할 때 재시도는 슬롯을 새로 잡으므로, 그때 시간당 상한에 닿아도 `RATE_LIMITED` 로
  끝난다 (일부러: 재시도 슬롯까지 미리 잡으면 대개 쓰지 않을 슬롯이 시간당 30회를 깎고, 받은 결과를 지키려면 앞 응답을 들고 있어야 해 메모리가 두 벌이 된다).
  client 는 응답을 `model_validate_json(response.content)` 로 바로 검증해 중간 dict 를 만들지 않는다. 37MB 가상 응답에서 최대 메모리가
  290MB → 246MB 로 줄었다 (2026-10-13 측정). 남은 대부분은 모델이 보존하는 모르는 필드(`extra='allow'`)다. 버리면 115MB 까지 줄지만
  응답 모델 규약([요청·응답 모델](../provider-conventions.md#요청응답-모델))과 달라 그대로 둔다.

## 요청 제한

`default_rate_limit` 은 3초·분당 12회·**시간당 30회**·쿨다운 30분(거듭 차단되면 두 배씩, 최대 4시간)이다. 분당 상한만으로는 수십 분에 걸친 누적을
막지 못해 시간당 상한과 늘어나는 쿨다운을 둔다. 막히지 않는다는 보장은 아니고, 막히면 에이전트가 네이버로 넘어가게 안내한다 (`docs/llm.md`).
폴링도 요청 제한 훅을 거친다.
재시도 술어는 `should_retry_skyscanner` 다. 공통 술어(`should_retry_idempotent`)에서 500 을 뺀다. 500 은 요청 값 때문에 늘 같은 응답이 오는 경우가 있어
(달력에 도시 IATA `SEL` → 500 `Application error processing RPC`) 다시 보내면 시간당 30회 중 3회를 같은 실패에 쓴다. 502·503·504 같은 중계 오류와
timeout·transport 오류는 그대로 다시 보낸다.
브라우저처럼 보이게 만들어 통과시키는 방법(쿠키 이식·TLS 지문 흉내·검사 스크립트 실행)은 우회라 쓰지 않는다 (위 [봇 차단](#봇-차단)).
검증할 때는 큰 검색(국내선 왕복)을 되풀이하지 않는다.

## API 변경 시 점검

번들 경로가 시장마다 다를 수 있으니(`www.skyscanner.co.kr` 홈 = `banana`) 홈 HTML 의 `runtime-main.*.js` 청크 표부터 본다. 헤더(`X-Skyscanner-*`)
→ 검색 본문 `legs` 모양 → 폴링 상태값 → `pricingOptions`·`agents` → 달력 본문의 `headers` 객체 순으로 확인한다.
