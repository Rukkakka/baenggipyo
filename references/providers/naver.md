# 네이버 항공권 개발 가이드

[CLI 사용법](../../docs/naver.md) · [개발 환경·검증](../development.md) · [공통 아키텍처](../architecture.md) · [provider 작성 규약](../provider-conventions.md) · [출력 계약](../output-contract.md) · [코드 형식](../code-style.md)

공통 규약에 없는, 네이버 항공권(flight.naver.com)에만 있는 구현과 예외를 적는다.

> **상태: 구현 완료 (2026-10-11).** 아래 사실은 flight.naver.com 의 JS 번들(빌드 `AKgbJRSghI-_MvY-JjFe3`)을 읽고 실제로 호출해
> 확인했다. 모두 로그인 없이 된다.

## 명령과 요청 흐름

| CLI 명령 | 주요 경로 | service 모듈 |
| --- | --- | --- |
| `search airports` | GraphQL `autocompleteLocations` 1회 | `airport` |
| `search flights` (편도·왕복·다구간) | 공항 해석(입력마다 `autocompleteLocations` 1회) → `route_kind` 로 국제선·국내선 가름 → `bookingPartners`(하루 한 번, 아래 판매처 이름) → `searchFlights` SSE 1회 (끝까지 읽음) | `airport` → `flight` |
| `search flights` (국내선 편도·왕복) | 공항 해석 → `bookingPartners`(하루 한 번) → `/flight/domestic/searchFlights` SSE 1회(가는 편), 왕복이면 캐시 조회 1회(오는 편) | `airport` → `domestic` |
| `flight` | `searchFlights` 캐시 조회(`selectedItineraries`) 1회 → GraphQL `getOtaList` 1회. 캐시가 만료됐으면 새 검색 1회 + 캐시 조회 1회 더 | `flight` |
| `flight` (국내선 편 1~2개, ID 가 `D-` 로 시작) | `bookingPartners`(하루 한 번) → 편마다 국내선 캐시 조회 1회(만료면 새 검색) → GraphQL `bookingOptions`(DOMESTIC) 1회 | `domestic` |
| `calendar` | 공항 해석 2회 → GraphQL `minPricesByDate` 1회 | `airport` → `calendar` |
| `explore` | 공항 해석 1회 → GraphQL `minPricesByDestination` 1회 (`size` 100) | `airport` → `explore` |

국내선은 국제선과 다른 API 와 화면 구조를 쓴다 (아래 [국내선](#국내선-flightdomesticsearchflights)). 처음에는 범위 밖으로 뒀다가 2026-10-11 에 구현했다.
모든 구간의 출발·도착이 국내(`autocompleteLocations` 의 `isDomestic: true`)면 국내선이고, 국내선 다구간과 국내·국제가 섞인 노선은 요청 전에
`ROUTE_NOT_SUPPORTED` 로 막는다 (`service/airport.py` `route_kind`).

## 소스 고유 구현

### 호스트와 헤더

- API 는 `https://flight-api.naver.com` 하나다 (`/api/runtime-config` 의 `api.flight`). 예전 실험의 `airline-api.naver.com/graphql`
  `internationalList` 쿼리와 `flight-api.naver.com/graphql` `airportAutocomplete` 쿼리는 사라졌다.
- **브라우저 `User-Agent` 와 `Accept-Language` 를 둘 다 보내야 한다.** 하나라도 빠지면 경로·본문과 상관없이 본문 없는 500
  (`content-length: 0`)이 온다 (`GET /` 포함). 둘 다 있으면 쿠키 없이 된다. 우회가 아니라 일반 브라우저 헤더다.
  `Origin`·`Referer` 는 `https://flight.naver.com` 으로 보낸다 (CORS 응답이 이 출처만 허용).
- GraphQL 은 `POST /graphql` 이다. 인트로스펙션은 막혀 있어(`GRAPHQL_VALIDATION_FAILED`) 쿼리 문자열은 번들에 든 것을 그대로 쓴다.
  잘못된 변수는 HTTP 200 + `errors[].extensions.code: BAD_USER_INPUT` 로 온다 (예: `DeviceType` 에 `pc` 를 주면 실패, `PC` 가 맞다).

### 항공권 검색 (`searchFlights`)

- `POST /flight/international/searchFlights`, `Accept: text/event-stream`. 응답은 201 + SSE 이고 `data:` 한 줄이 이벤트 하나다.
- **이벤트마다 그때까지의 결과 전체(누적 스냅숏)** 가 온다. 판매처(파트너) 하나가 끝날 때마다 이벤트가 하나씩 오고
  (`status.completedPartnerCount` / `requestedPartnerCount`, 실측 20곳), 마지막 이벤트에 `status.isCompleted: true` 다.
  마지막 이벤트만 쓰면 된다. 편도 ICN→NRT 9초, 왕복 SEL↔TYO 17초.
- **스트림은 끝까지 읽어야 한다.** 첫 이벤트만 받고 끊으면 서버가 검색을 버려, 바로 뒤 캐시 조회가 `isExpired: true` 다.
  이벤트 간격은 길어야 5초였다 (ICN→NRT 20곳, 2026-10-11). 그래도 다음 이벤트가 읽기 시간 상한(client `timeout` 20초) 안에 오지 않거나
  전체 60초가 지나면 받은 마지막 이벤트로 끝내고(`isCompleted: false`) `notes` 로 알린다 (`api/stream.py`). 60초는 스트림이 열린 뒤부터 잰다.
  요청 제한 훅이 슬롯을 기다린 시간까지 세면 앞 명령 때문에 검색이 잘린다 (2026-10-12 실측: 슬롯 대기 55초 뒤 ICN↔NRT 왕복이 판매처 12/20곳에서
  끊겼고, 고친 뒤 같은 조건에서 끝까지 받았다).
  받은 이벤트를 남기는 것은 이 두 시간 상한뿐이다. 연결이 끊긴 것 같은 다른 전송 오류는 받은 이벤트를 버리고 `naver_retry` 로 검색을 한 번 더
  한다. 늦은 판매처는 다시 해도 늦지만 끊긴 연결은 대개 일시적이라 다시 하면 끝까지 받기 때문이다 (일부러 그렇게 둔다, 2026-10-11 리뷰).
  끊는 순간 서버가 검색을 버리는 것도 확인했다: 새 검색을 2초에 끊으면(판매처 11/20곳) 그 version 의 캐시 조회가 `isExpired` 였다 (ICN→OKA 12/4).
- 응답이 크다. 이벤트마다 항공편 목록(`itineraries`)을 통째로 다시 보내기 때문이다. 편도 `limit: 200` 은 이벤트 20개 27MB,
  왕복 `limit: 20` 은 4.9MB 였다. `limit` 은 운임 묶음(`fareMappings`) 수만 줄이고 `itineraries` 는 그대로다. 읽으면서
  마지막 이벤트만 남기고 버린다.
- 요청 본문 (번들의 zod 스키마):

  | 필드 | 값 |
  | --- | --- |
  | `tripType` | `OW`·`RT`·`MD`(다구간) |
  | `device` | `pc`·`mobile` |
  | `seatClass` | `Y`(일반석)·`P`(프리미엄일반석)·`C`(비즈니스석)·`F`(일등석) |
  | `adultCount`·`childCount`·`infantCount` | 0 이상 정수 |
  | `isNonstop` | 직항만 |
  | `itineraries[]` | `departureLocationCode`·`arrivalLocationCode`(IATA), `departureLocationType`·`arrivalLocationType`(`airport`·`city`), `departureDate`(`YYYYMMDD`) |
  | `openReturnDays` | 0 |
  | `initialRequest` | 새 검색이면 `true`, 캐시 조회면 `false` + `version`(`status.__v`). `version` 을 빼면 그 조건의 가장 새 캐시 ([캐시 version](#판매처별-가격-flight)) |
  | `flightFilter` | `{filter: {...}, limit, skip, sort: {키: 1 또는 -1}}`. 필터 키: `airlines`, `departureAirports`, `arrivalAirports`, `departureTime`, `fareTypes`, `flightDurationSeconds`, `viaCount`, `minFare: {min, max}`, `selectedItineraries` 등 |

  정렬 키는 번들에 `adultMinFare`(가격)·`departureTime`·`returnTime`·`departureDurationSecond`(소요 시간)가 보인다. baenggipyo 가 쓰는
  `adultMinFare`·`departureTime`·`departureDurationSecond` 는 서버가 적용한다 (2026-10-11 ICN→NRT 실측: 출발 시각순, 소요 시간순이고
  소요 시간이 같으면 가격순). 그래서 출발시각·소요시간 정렬은 받은 순서를 그대로 쓰고 다시 확인하지 않는다.
- 마지막 이벤트 구조: `uniqueId`, `isExpired`, `status`, `itineraries[]`, `fareMappings[]`, `popularFlights[]`.
  - `status`: `searchKey`(검색 조건에서 만든 키, 예 `OWY100PN20261115ICNANRTA`), `__v`, `expireAt`(검색 뒤 15분), `airlinesCodeMap`(코드 →
    한국어 이름), `airportsCodeMap`, `fareTypesCodeMap`(운임 종류 코드 → 이름·분류), `lowestFare`, `priceRange`.
  - `itineraries[]`: 한 방향 여정 하나. `itineraryId`(예 `20261115ICNNRTZE0605`, 경유는 `+` 로 이음), `sequence`(1 = 가는 편, 2 = 오는 편),
    `duration`(초), `segments[]` — `departure`·`arrival`(`airportCode`, 현지 시각 `dateTime` `YYYYMMDDHHmm`), `marketingCarrier`·
    `operatingCarrier`(`airlineCode`, `flightNumber`), `flightDuration`·`groundDuration`(초), `aircraftCode`.
  - `fareMappings[]`: 항공권 하나(여정 조합)의 운임 목록. `itineraryIds` 는 여정 ID 를 `-` 로 이은 것이다 (왕복 예
    `20261115ICNNRTET0672-20261120NRTICNET0673`). 이 값을 항공권 ID 로 쓴다. `fares[]` 는 판매처 × 운임 종류마다 하나:
    `partnerCode`(판매처), `fareType`, `adult`·`child`·`infant` 의 `totalFare`·`tax`·`qCharge`, `baggageFeeType`(`FREE`·`CHARGE`·`UNKNOWN`),
    `isConfirmed`, `reservationUrl`.
- **운임 종류(`fareType`)에 카드 할인이 섞인다.** `A01` 은 "성인/모든 결제수단", `A01/Bxx` 는 특정 카드·간편결제 할인
  (`A01/B80` "현대 M2/M3 Edition2(이용실적 충족시)" 처럼 조건부도 있음), `A07` 은 장애인 운임이다. 사이트의 최저가 표시(`lowestFare.direct`)
  는 카드 실적 할인가였다 (ICN→NRT 11/15: 164,790원, `A01` 최저 172,238원). 실측 175개 묶음 모두 `A01` 운임이 있었다.
  **대표 가격은 `A01`(모든 결제수단) 최저가다** (2026-10-11 결정). 조건이 붙은 가격을 대표 가격으로 쓰지 않는다. 카드·간편결제 할인 최저가는
  네이버 전용 필드로 함께 준다 (`NaverItinerary`): `card_discount_price_krw`(1인), `card_discount_name`(`fareTypesCodeMap` 의 이름, 예 "현대
  M2/M3 Edition2(이용실적 충족시)"), `card_discount_category`(예 "신용카드(실적충족/청구할인)"). `A01` 보다 싼 할인 운임이 없으면 세 키를 뺀다
  (해당 없음). 장애인 운임(`A07`) 같은 대상 제한 운임은 어느 쪽에도 쓰지 않는다.
- 검색 결과의 `reservationUrl` 은 늘 빈 문자열이다 (7,830개 모두). 예약 링크는 아래 캐시 조회에서 채워진다.

### 판매처별 가격 (`flight`)

1. 같은 검색 본문에 `initialRequest: false`, `version: <status.__v>`, `flightFilter.filter.selectedItineraries: [<여정 ID…>]` 를 넣어 다시
   부른다. 서버 캐시에서 바로(0초) 이벤트 하나가 오고, 그 항공권의 `fares[]` 에 `reservationUrl` 이 채워진다 (실측 118개).
   캐시가 없거나 만료됐으면 `isExpired: true` 와 빈 결과가 바로 온다 → 검색을 처음부터 다시 한다. 캐시 키는 검색 조건이라 다른 프로세스에서
   만든 검색도 15분 안이면 같은 조건으로 찾을 수 있다. 그래서 `flight` 의 ID 에는 검색 조건(여정·좌석·승객)을 함께 담아야 한다.
   - **캐시 version**: 서버는 같은 조건의 새 검색마다 `status.__v` 를 올리고 version 별 캐시를 따로 두어 각각 약 15분 뒤 지운다. 다른 사용자도 찾는 노선은
     version 0 캐시가 먼저 사라져, version 0 으로 물으면 방금 검색했어도 `isExpired` 가 온다 (ICN→NRT 11/15: v0 만료, v1·v2 있음, 생략하면 v2.
     김포↔제주 12/20: v0·v1 있음, 생략하면 v1. 2026-10-11 실측). 전에는 `flight` 가 version 0 으로 물어 매번 다시 검색하고 '만료' 를 알렸다. 그래서 국제선·
     국내선 `flight` 의 첫 캐시 조회는 version 을 빼 가장 새 캐시를 받는다 (`CACHE_LATEST_VERSION`). 같은 검색 안에서 잇는 조회(국내선 오는 편, 다시 검색한
     뒤의 조회)는 그 검색이 준 version 을 쓰고, `__v` 가 빠졌으면 version 0 이 아니라 생략(가장 새 캐시)으로 묻는다 (김포→제주 11/20 이 v32 일 때 v0 은
     `isExpired`, 생략하면 v32, 2026-10-11 실측).
   - **version 을 빼고 물을 때**: 캐시가 아직 없는 조건이면 만료가 아니라 서버가 그 자리에서 검색해 끝난 결과를 준다 (ICN→UBJ 2027-03-10: 4.1초, v0,
     판매처 20/20). 다른 사람이 돌리는 검색이 진행 중이면 그 검색이 끝날 때까지 이어 보낸다 (ICN→FUK 12/3: 새 검색 3초 뒤 물어 5.2초 뒤 완료,
     2026-10-11 실측). 그래서 이 조회가 덜 끝난 결과(`isCompleted: false`)를 주는 것은 시간 상한(60초)에 끊겼을 때뿐이다.
     끊긴 결과에서 그 항공권을 찾으면 응답한 판매처의 운임만 담겨 있다. 매진이 아니니 그대로 내되 `notes` 로 알린다 (다시 검색이 끊긴 것도, 국내선
     `flight` 도 같다).
   - **검색 미완료**: 다시 묻는 검색이 시간 상한에 끊겨 그 항공권이 없거나 캐시가 만료로 오면 매진으로 단정하지 않고 `SearchIncompleteError`
     (`UPSTREAM_ERROR`, `reason: SEARCH_INCOMPLETE`)로 낸다. `FARE_UNAVAILABLE` 의 hint(후보에서 빼라)를 따르면 아직 파는 항공권을 버리기 때문이다
     (ICN→TAK 2027-03-03 을 1초에 끊어 판매처 1/20곳 → 캐시 `isExpired` → `SEARCH_INCOMPLETE` 확인, 2026-10-11). 국내선 `_find_flight` 도 같다
     (`isComplete: false` 인 응답에 편이 없을 때).
   - 같은 조건으로 새 검색을 곧바로 거듭 보냈을 때 500 이 오고, 몇 분 동안 다른 국내선 조건도 500 이었다 (2026-10-11 21:20 쯤, 한 번 관찰). 검증할 때 같은
     조건의 새 검색을 연달아 보내지 않는다.
2. GraphQL `getOtaList`(`bookingOptions(input: {…, searchType: INTERNATIONAL})`) 에 `searchParams`(`itinerary[]`, `person`, `trip`,
   `fareType`), `otaFares[]`(`otaId`=`partnerCode`, `fare`, `fareType`, `bookingUrl`=`reservationUrl`), `device: PC`, `scheduleIds`,
   `curations`, `isFareLoadCompleted` 를 넣으면 판매처 이름(`expName`)·순위·`bookingUrl`·`redirectUrl` 이 온다.
   `reservationUrl` 을 비운 채로 보내면 빈 목록이 온다.
   - `bookingUrl` 은 네이버 클릭 추적 링크(`tivan.naver.com/fotac/…`), `redirectUrl` 은 판매처 랜딩 URL 이고 둘 다 검색 요청 ID·시각이 들어 있다.
     `bookingUrl` 은 경로 전체가 불투명한 값이라 뗄 수 있는 토큰이 없고, 열면 판매처로 307 리디렉트된다. 사이트가 판매처로 넘기는 링크라
     `offers[].url` 로 그대로 낸다 ([내보내지 않는 정보](../output-contract.md#내보내지-않는-정보)의 `url` 예외). 검색 캐시(15분)가 지난 뒤에도
     열린다 (발급 17분 뒤에도 같은 판매처로 307, 2026-10-11 ICN→NRT 와이페이모어). 더 긴 시간은 재지 않았다.
   - 응답에 판매처 연락처(`csInfo`)·사업자 정보가 있지만 내보내지 않는다.
- 판매처 이름만 필요하면 GraphQL `bookingPartners`(변수 없음, `otaId`→`expName` 26곳)로도 된다.

### 공항 검색 (`autocompleteLocations`)

GraphQL `autocompleteLocations(airportOnly: Boolean!, query: String!)`. 결과는 `__typename` 으로 나뉜다: `SearchCity`(`cityCode`, `airports[]`),
`SearchAirport`(`airportCode`, `cityCode`), `SearchCountry`, `SearchContinent`. '도쿄' → `TYO`(NRT·HND), `NRT`, `HND`, `IBR`.

### 날짜별 최저가 (`minPricesByDate`)

- 변수: `departureLocationCode`·`departureLocationType`, `arrivalLocationCode`·`arrivalLocationType`, `departureDate`, `groupByDepartureDate`,
  `isNonstop`, `tripType`. 사이트는 `groupByDepartureDate: false` 로 부른다.
- 편도는 `departureDate` 없이 한 번에 약 1년치를 준다 (ICN→NRT 2026-10-12 ~ 2027-10-09, 값이 있는 205일만).
- 왕복은 `departureDate` 를 주면 그날 출발의 오는 날 11일치(출발일 ~ +10일)를 준다.
- **실시간 가격이 아니라 최근 검색에서 모은 값이다.** 방금 검색한 날은 그 검색의 최저가(카드 할인가 포함, 164,790원)와 같았다.
  출력에 이 사실을 `notes` 로 알린다.

### 어디든지 (`minPricesByDestination`)

- 네이버에도 있다 (`/flights/everywhere/monthly/{출발}` 페이지). 변수: `departureLocationCode`, `departureMonths`(`["202611"]`), `tripType`,
  `tripDays`, `isNonstop`, `isDomestic`, `continentIds`, `countryCodes`, `price`, `size` 등.
- 결과마다 목적지 공항(`destination.airportCode`, 도시 이름), `minPrice`, `departureDate`·`returnDate`·`tripDays`, `stops`, `airlineCodes`, 출발 공항.
  서울 출발 11월 왕복 `size: 20` 에 국내선(제주·부산)이 섞여 왔다 → 국제선만 보려면 `isDomestic: false`.
- 가격은 달력과 같이 모은 값이다.

### 구현 메모 (2026-10-11 실측)

- **공항 해석** (`service/airport.py` `resolve_location`): 3글자 코드는 같은 코드의 공항을 먼저(`ICN`·`BKK`), 없으면 도시(`SEL`·`TYO`·`OSA`)를
  고른다. 자동완성은 코드를 넣으면 같은 코드의 도시·공항을 함께 준다 ('ICN' → 도시 ICN + 공항 ICN, 'BKK' → 도시 BKK(BKK·DMK) + 공항 BKK·DMK).
  이름은 이름이 똑같은 도시, 없으면 공항, 후보가 하나뿐이면 그것을 쓰고 아니면 `AIRPORT_AMBIGUOUS` (두 소스 공통 `core.flight.pick_place`). 국내선 판정도 자동완성의 `isDomestic` 으로 한다.
  없는 3글자 코드에는 자동완성이 대개 후보를 주지 않지만(`ICM`·`NTR`·`GPM` 등 0개), 이름 앞부분처럼 읽히면 다른 곳을 하나 준다 (`SEO` → 도시 `SEL`
  서울 하나). 전에는 후보가 하나라 경고 없이 서울로 찾았다. 3글자 입력이 어느 후보의 코드·이름과도 맞지 않으면 후보가 하나여도 쓰지 않고
  `AIRPORT_NOT_FOUND` 로 내며 hint 에 비슷한 후보를 적는다 (2026-10-11 실측).
- **시각**: 구간의 `dateTime` 이 없고 `date`·`time` 만 오는 여정이 있다 (왕복 ET672 등). `NaverPointModel.local_date_time` 이 둘을 합친다.
- **편명**: `flightNumber` 는 앞에 0 이 붙는다 (`0605` → `ZE605` 로 낸다). 운항사 `flightNumber` 는 빈 문자열로 오기도 한다.
- **판매처 이름**: `bookingPartners` 는 26곳뿐이라 검색 판매처 일부(`ATK201`·`EDR215` 등)는 이름이 없다 (ICN→NRT 50건 중 35건의 대표 운임 판매처가
  이름 없음, 2026-10-11). 대표 운임과 성인 운임·승객 합계·수하물이 모두 같은 운임이 이름을 아는 판매처에도 있으면 그 이름만 `cheapest_seller` 로
  쓰고(가격·수하물은 대표 운임 값), 없으면 `null` 이다. `flight` 는 `getOtaList` 의 `expName` 을 쓰므로 이름이 다 있다.
  검색 응답(`status`)에는 판매처 이름표가 없어 따로 받아야 한다. 가격이 아닌 이름표라 상태 디렉터리(`<state_dir>/naver/booking_partners.json`)에
  하루 동안 두고 다시 쓴다. 그래서 검색마다 요청이 1회 줄어든다 (`partner_names`). 표시용 이름이라 GraphQL 이 실패하거나 빈 이름표(`null`·`[]`)가 와도
  검색을 막지 않고 이름 없이 이어 가며, 빈 이름표는 저장하지 않는다 (저장하면 하루 동안 모든 판매처 이름이 빠진다). 실제 응답은 늘 26곳이었다.
- **항공권 ID**: 서버 캐시 키가 검색 조건이라 `flight_id()` 가 조건을 함께 담는다
  (`Y-100-0-ICNaNRTa20261115_NRTaICNa20261120~20261115ICNNRTZE0605-20261120NRTICNWE0504`). 항공사 필터·정렬은 캐시 키가 아니라 넣지 않는다.
- **정렬**: 세 정렬 모두 서버가 건다 (위 정렬 키). 서버 가격순(`adultMinFare`)은 카드 할인을 포함한 최저가 기준이라 대표 가격(`A01`) 순서와 어긋날 수 있다. 어긋나면 `notes` 로 알리고
  다시 세우지 않는다 (서버가 `limit` 로 잘라 준 결과라 다시 세워도 빠진 항공권이 돌아오지 않는다).
- **판매처 목록**: 사이트처럼 `active.realtime` 인 판매처만 낸다. 링크(`tivan.naver.com`)가 하나에 2~3KB 라 `--limit`(기본 10)으로 자르고,
  대표 가격 기준에 맞춰 모든 결제수단 운임을 먼저 둔다 (왕복 한 건에 판매처 운임 64개).
  `flight` 의 `offer_count` 는 낸 판매처를 코드(`otaId`)로 센 수다. `search flights` 는 실시간 여부를 몰라 운임 묶음의 판매처를 모두 세므로 실시간이
  아닌 판매처만큼 크다 (TW241 11/15: 검색 24곳, `flight` 23곳, `ATW133` 이 `realtime: false`. 판매처 코드와 이름은 1:1 이었다, 2026-10-11). 뺀 수는
  `notes` 로 알린다.
  `flight` 의 대표 가격·`cheapest_seller`·카드 할인도 `offers` 에 낸 운임(실시간 판매처가 돌려준 행)으로 정한다. 검색 운임 전체로 정하면 실시간이 아니라 뺀
  판매처나 판매처 조회에 넘기지 못한 운임이 가장 쌀 때, 그 가격을 파는 판매처가 `offers` 에 없고 `cheapest_seller` 가 null 이 된다 (2026-10-13 리뷰).
  `bookingOptions` 가 null 인 응답은 빈 목록으로 내지 않고 `UPSTREAM_ERROR` 로 낸다. 빈 `offers` 는 파는 곳이 없다고 읽히기 때문이다 (국내선과 같다).
- **어디든지**: 서버는 인기순이라 100곳을 받아 가격순으로 자른다. 국제선 목적지만 묻는다(`isDomestic: false`). 나라 이름은 주지 않는다.
- **달력 왕복**: `groupByDepartureDate: true` + `tripDays: [n, n]` 이면 출발일마다 n일 왕복 최저가다 (ICN→NRT 349일, `tripDays [4, 4]` 299일).

### 국내선 (`/flight/domestic/searchFlights`)

2026-10-11 실측. 번들의 `useDomesticFlights`·`useDomesticFlight` 훅과 국내선 상세 페이지를 읽고 확인했다.

- **요청**(zod 스키마): `type: "domestic"`, `device`, `fareType`(`YC` 전체·`C`·`Y`·`D`·`L`·`NR`·`NB`), `itineraries[]`(`departureAirport`·`arrivalAirport`·
  `departureDate` YYYYMMDD — 공항 코드만), `person`(`adult`·`child`·`infant`), `tripType`(OW·RT), `initialRequest`·`version`, `flightFilter`.
  `fareType` 을 `Y` 로 보내도 캐시 키(`status.flightSearchKey`)는 `YC|…` 라, 좌석은 `flightFilter.filter.seatClass`(배열)로 건다. 항공사는
  `filter.segment__airlineCode`, 방향은 `filter.type`(`departure`·`arrival`). 정렬은 `{minFare: 1}`, `{"segment.departure.time": 1, minFare: 1}`,
  `{duration: 1, minFare: 1}`.
- **응답**: 국제선과 같은 SSE 이고 완료는 `status.isComplete`, 결과는 `flights[]` 다. 김포→제주 편도 30KB, 이벤트 1개, 0.1초 (방금 누가 검색한 캐시였을
  수 있다). 왕복 첫 검색 5.6초, 오는 편 캐시 조회는 바로.
- **편**: `itineraryId`(`202611151910GMPCJUWE6503` — 출발 일시 12자리·출발·도착 공항·항공사·편명), `type`, `seatClass`(`NR` 특가석-환불불가, `NB` 특가석-
  수하물유료, `L` 특가석, `D` 할인석, `Y` 일반석, `C` 비즈니스석 — 번들 이름표), `seatCount`(남은 좌석), `duration`(분), `segment`(하나, 모두 직항),
  `fares[]`.
- **운임**: 판매처(`agtCode`)마다 `adultFare`·`childFare`·`aFuel`·`cFuel`·`aTax`·`cTax`·`publishFee`·`bookingClass`·`etc`·`discountFare`(카드 할인).
  1인 총액은 사이트 계산식대로 운임 + 유류할증료 + 세금 + 발권 수수료다 (`fare_per_person`. `adultTotalFare` 와 같다). 사이트도 유아 운임은 넣지 않는다.
  `childFare: 0` 은 그 판매처가 소아 운임을 주지 않는 것이다 (소아 1명으로 찾은 3노선 운임 1,800개에는 없었다, 2026-10-11). 그래서 유아가 있거나
  소아 운임이 없으면 합계를 모르는 것으로 보고 `total_price_krw` 를 null 로 둔다 (`fare_total`).
- **카드 할인**: `discountFare.adultDiscountFare` 와 `discountInfo.cardType.name`. 국제선과 같이 대표 가격으로 쓰지 않고 `card_discount_*` 로 낸다.
- **판매처 조회**: `bookingOptions(input: {…, searchType: DOMESTIC})`. `otaFares[]` 는 판매처마다 고른 편 전부의 운임을 묶고(`otaId`, `fare` 합계, `fareType: A01`,
  `bookingParams[]` — 편마다 번들 함수 그대로: 항공사·편명·출발/도착 공항과 일시·`bookingClass`·`etc`·남은 좌석·운임 필드), 왕복에서 두 편의 최저가 판매처가
  다르면 그 둘을 `combinedFares` 로 넘긴다. `scheduleIds` 는 `항공사-편명-출발YYYYMMDDHHmm-좌석등급`. 응답은 `otaList`(한 판매처)와 `combined`(편마다 따로).
  응답의 `fare` 는 보낸 `fare` 를 그대로 돌려준 값이다 (2026-10-11 실측). 보내는 값은 사이트와 같게(유아 빼고) 두고, 합계를 모르는 판매처(위 `fare_total`)의
  `offers[].total_price_krw` 는 null 로 낸다.
  링크는 국제선처럼 `tivan.naver.com` 이고 0.5~0.9KB 다.
- **검증**: 김포↔제주 11/15~11/17, 가는 편 WE6503 42,000원 + 오는 편 RS6306 59,900원 = 101,900원이 스카이스캐너의 같은 두 편 조합(101,900원)과 같았다.
  `flight` 두 편은 노랑풍선 101,900원이 최저였다.
- **결과 페이지**: `/flights/domestic/GMP-CJU-20261115/CJU-GMP-20261117?adult=1&fareType=YC` 가 "김포-제주 왕복" 으로 열린다.

## Action id 목록

GraphQL 루트 필드 이름을 쓴다 ([명명 규칙](../provider-conventions.md#action-id-명명)). REST 는 경로 규칙대로.

| 요청 | Action id |
| --- | --- |
| `POST /flight/international/searchFlights` (SSE) | `flight_international_search_flights` |
| GraphQL `autocompleteLocations` | `autocomplete_locations` |
| `POST /flight/domestic/searchFlights` (SSE) | `flight_domestic_search_flights` |
| GraphQL `bookingOptions` (국제선, 사이트 오퍼레이션 이름 `getOtaList`) | `booking_options` |
| GraphQL `bookingOptions` (국내선, searchType DOMESTIC) | `booking_options_domestic` (루트 필드가 같아 뜻으로 구분) |
| GraphQL `bookingPartners` | `booking_partners` |
| GraphQL `minPricesByDate` | `min_prices_by_date` |
| GraphQL `minPricesByDestination` | `min_prices_by_destination` |

SSE 응답은 한 줄씩 읽어 마지막 이벤트만 검증하는 단계가 따로 필요하다. [모듈 구성](../provider-conventions.md#모듈-구성)대로 `api/` 안의
별도 모듈(`api/stream.py` 등)로 둔다.

## 원본 금액 단위

모두 원 정수다. 검색 운임은 **승객 유형별 1인 요금**(`adult.totalFare` 등, 세금 `tax`·유류할증료 `qCharge` 포함 총액)이라 총액은
`승객 수 × 1인 요금` 을 더해 만든다 (모르는 값을 추측하는 계산이 아니다). 달력·어디든지의 `minPrice` 는 1인 기준으로 보이며 구현할 때 확인한다.

## 페이지네이션과 조건 처리

- 결과는 폴링 없이 SSE 한 번이다. `flightFilter.limit`·`skip` 으로 운임 묶음을 자르고, `limit` 은 응답 크기에 직접 영향을 준다.
- 직항(`isNonstop`)·좌석 등급(`seatClass`)·항공사·출발 시각·경유 횟수는 서버 필터로 보낸다. 서버가 건 조건은
  [공통 기준](../provider-conventions.md#서버-필터와-클라이언트-후처리)대로 다시 거르지 않는다.
- 운임 종류(`fareTypes` 필터)로 `A01` 만 달라고 할 수 있는지는 구현할 때 확인한다.

## 요청 제한

429·차단 한계는 재지 않았다. `default_rate_limit`(1초·분당 30회·쿨다운 15분)은 그대로 둔다.
검색 한 번이 판매처 20곳을 서버에서 부르고 수 MB 를 받으므로, 명령 하나가 검색을 여러 번 하지 않게 한다.

## API 변경 시 점검

번들의 GraphQL 문서(`{kind:"Document",…}`)와 `searchFlights` 본문 zod 스키마가 출발점이다. 헤더 조건(User-Agent + Accept-Language) →
SSE 이벤트 구조와 완료 표시 → 캐시 조회(`initialRequest`·`version`) → `fareType` 코드 → `getOtaList` 변수 순으로 확인한다.
