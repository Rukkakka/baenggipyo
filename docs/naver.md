# 네이버 항공권 CLI 사용법 (`baenggipyo naver`)

[설치](install.md) · [공통 사용 규칙](common.md) · [개발 가이드](../references/providers/naver.md)

> 금액은 **원**, 시각은 **각 공항 현지 시각**이다. 출력 형식·누락값·에러 대응·요청 제한 같은 공통 규칙은
> [공통 사용 규칙](common.md)에 있고, 이 문서는 네이버에만 있는 내용을 적는다.
>
> 국제선과 국내선을 모두 찾는다. **국내선 왕복은 결과 모양이 다르다** ([국내선](#국내선-김포제주-등)). 국내선 다구간과 국내·국제가 섞인 노선은
> `ROUTE_NOT_SUPPORTED` 이니 [스카이스캐너](skyscanner.md)를 쓴다.

## 한눈에 보기

| 명령 | 하는 일 | 무엇을 넣나 | 요청 수 | 다음에 쓰는 곳 |
| --- | --- | --- | --- | --- |
| `search airports` | 공항·도시 IATA 코드 찾기 | 이름 또는 코드 | 1 | `search flights`, `calendar`, `explore` |
| `search flights` | 편도·왕복·다구간 항공권 (국내선은 편도·왕복) | 출발·도착과 날짜 | 공항 입력마다 1 + 1~2 (국제선 10~20초, 국내선 수 초) | `flight` |
| `flight` | 항공권의 판매처별 가격·예약 링크 | 항공권 ID (국내선 왕복은 둘) | 2~3 (만료됐으면 더) | — |
| `calendar` | 한 달의 날짜별 최저가 (추정가) | 출발·도착과 달 | 3 | `search flights` |
| `explore` | 어디든지: 목적지별 최저가 (추정가) | 출발지와 달 | 2 | `calendar`, `search flights` |

### 보통 이렇게 이어서 쓴다

```
항공권 찾기   search airports ─▶ search flights ─▶ flight
날짜 고르기   calendar ─▶ search flights --depart <싼 날> ─▶ flight
목적지 고르기 explore ─▶ calendar / search flights
```

### 출발지·도착지 넣는 법

모든 명령이 IATA 코드나 이름을 받는다.

- 3글자 코드는 같은 코드의 **공항**을 먼저 고르고, 공항이 없으면 **도시**를 고른다. `ICN`·`NRT`·`BKK` 는 공항, `SEL`·`TYO`·`OSA` 는 도시다.
  도시 코드는 그 도시의 모든 공항을 찾는다 (`SEL` = 인천·김포, `TYO` = 나리타·하네다).
- 이름은 이름이 똑같은 도시, 없으면 공항을 고른다 (`도쿄` → `TYO`). 그 도시의 공항이 하나뿐이면 그 공항으로 찾는다 (`인천` → 인천국제공항 `ICN`. 도시 인천으로
  찾으면 네이버가 김포 출발까지, 도시 김포로 찾으면 인천 출발까지 준다). 정하지 못하거나 이름이 똑같은 곳이 여럿이면 `AIRPORT_AMBIGUOUS` 와 후보를 준다
  (`포틀랜드` → `PDX`·`PWM` 두 도시).
- 없는 3글자 코드는 비슷한 후보가 하나뿐이어도 그곳으로 찾지 않고 `AIRPORT_NOT_FOUND` 다. `hint` 에 비슷한 후보를 적는다 (`SEO` → 후보 `SEL` 서울).
- 대문자 4글자(ICAO `RKSI`, 대문자로만 쓴 이름 `JEJU`)는 요청 없이 `AIRPORT_NOT_FOUND` 다 ([공통 규칙](common.md#출력-규칙)). 한글 도시
  이름(`제주`)이나 IATA 코드로 다시 넣는다.
- 공항·도시를 고르는 데 입력마다 요청이 1회 든다.

---

## 1. `search airports` — 공항·도시 코드 찾기

| 인자·옵션 | 넣을 수 있는 값 |
| --- | --- |
| `<이름>` | 공항·도시 이름 (한국어·영어) 또는 코드 |
| `--limit` | 후보 수 (1~20, 기본 10) |

반환: `query`, `returned`, `airports[]` — `code`, `type`(`공항`·`도시`), `name`, `city`, `country`, `airports`(도시만, 속한 공항 코드), `domestic`.

```bash
baenggipyo naver search airports 오사카
```

## 2. `search flights` — 항공권 찾기

| 분류 | 옵션 | 넣을 수 있는 값 |
| --- | --- | --- |
| 노선 | `<출발>`, `<도착>` | IATA 공항·도시 코드 또는 이름 |
| | `--depart` | 가는 날 `YYYY-MM-DD` (오늘 이후) |
| | `--return` | 오는 날. 주면 왕복 |
| | `--leg` | 다구간 구간 `출발-도착-YYYY-MM-DD`. 구간마다 반복. `<출발> <도착>`·`--depart`·`--return` 과 함께 쓰지 않는다 |
| 승객 | `--adults` / `--children` / `--infants` | 성인(1~9, 기본 1)·소아(만 2~11세)·유아(만 2세 미만, 성인 수 이하). 모두 9명까지 |
| 조건 | `--cabin` | `일반석`(기본)·`프리미엄일반석`·`비즈니스석`·`일등석`. 영어 `economy`·`premium_economy`·`business`·`first` 도 받는다 |
| | `--direct` | 직항만 |
| | `--airline` | 항공사 IATA 코드만 (`KE,OZ`). 반복하거나 쉼표 |
| | `--depart-time` | 가는 편(다구간은 첫 구간) 출발 시각대 `시작시-끝시` (`06-12`, `18-24`). 경계는 0·6·9·12·15·18·21·24시만 받고 시작 시는 넣고 끝 시는 뺀다. 자정을 넘는 시각대(`21-06`)는 둘로 나눠 찾는다 |
| | `--return-time` | 오는 편 출발 시각대. 왕복(구간이 둘인 검색)에서만, 값은 `--depart-time` 과 같다 |
| 결과 | `--sort` | `가격`(기본)·`출발시각`·`소요시간`. 영어 `price`·`departure`·`duration` 도 받는다 |
| | `--limit` | 돌려줄 항공권 수 (1~50, 기본 10) |
| | `--brief`(기본) / `--full` | 기본은 고르는 데 필요한 키만 낸다. `--full` 이면 구간별 공항·시각·기종, 항공권 `url` 까지 ([응답 줄이기](common.md#응답-줄이기)) |

조건(직항·좌석·항공사·출발 시각대)과 정렬은 네이버 서버가 건다. "오전 출발"처럼 시각대가 정해졌으면 `--limit` 을 늘려 직접 고르지 말고
`--depart-time`·`--return-time` 을 쓴다 (서버가 그 시각대에서 싼 순으로 준다).

반환: `search`(검색 조건과 원 사이트 결과 페이지 `url`), `returned`, `itineraries[]`
([공통 항공권 필드](common.md#공통-항공권-필드)). 네이버만 주는 필드:

| 필드 | 의미 |
| --- | --- |
| `card_discount_price_krw` | 특정 카드·간편결제로만 받는 **성인 1인** 최저가. 대표 가격보다 싼 할인이 없으면 키가 없다 |
| `card_discount_name` | 그 결제수단과 조건 (예: `성인/KB국민카드(이용실적 충족시)`) |
| `card_discount_category` | 분류 (`신용카드(즉시할인)`, `신용카드(실적충족/청구할인)`, `간편결제`) |

- **대표 가격**(`price_per_adult_krw`, `total_price_krw`)은 어떤 결제수단으로도 받을 수 있는 운임의 최저가다. 카드 할인가는 해당 카드로 결제하고
  실적 조건 등을 채워야 받을 수 있으니, 사용자에게 조건과 함께 알린다.
- 모든 결제수단 운임이 없으면 대표 가격이 `null` 이다. 카드 할인 운임만 있으면 `card_discount_*` 로만 살 수 있고, 그것도 없으면 가격을 모르니
  `flight` 로 판매처별 운임을 본다. 어느 쪽인지 `notes` 로 알린다.
- `total_price_krw` 는 승객 유형별 1인 운임 × 인원의 합이다.
- `--sort 가격` 은 대표 가격(`price_per_adult_krw`) 순이다. 네이버 가격순은 카드 할인을 포함한 최저가 기준이라 카드 할인이 없는 싼 항공권이
  뒤로 밀리므로, `--limit` 의 3배를 받아 대표 가격 순으로 다시 세운 뒤 자른다. 대표 가격이 `null` 인 항공권은 맨 뒤다.
- `cheapest_seller` 는 네이버가 이름을 알려 주지 않는 판매처면 `null` 이다.
- `aircraft`(기종)는 네이버가 준 그대로라 이름(`Airbus A321`)과 IATA 기종 코드(`738`)가 섞여 온다. 모르면 `null` 이다.
- `--limit` 만큼 받았으면 더 있을 수 있다고 `notes` 로 알린다. 늘리면 응답이 커지고 느려진다.
- 판매처가 60초 안에 다 응답하지 않으면 받은 데까지 돌려주고 `notes` 로 알린다.

```bash
baenggipyo naver search flights ICN NRT --depart 2026-11-15 --return 2026-11-20
baenggipyo naver search flights SEL OSA --depart 2026-12-01 --direct --adults 2 --limit 5
baenggipyo naver search flights --leg ICN-NRT-2026-11-15 --leg KIX-ICN-2026-11-20
```

### 국내선 (김포↔제주 등)

출발·도착이 모두 국내면 국내선 API 로 찾는다. 네이버 화면처럼 **가는 편과 오는 편을 따로 고르는** 구조라 왕복은 결과 모양이 다르다.

- 편도 반환: 국제선과 같다 — `search`, `returned`, `itineraries[]`.
- 왕복 반환: `search`, `legs[]` — 방향마다 `direction`(`가는 편`·`오는 편`), `origin`, `destination`, `date`, `returned`, `itineraries[]`.
  `itineraries` 가 맨 위에 없다.
- `itineraries[]` 의 항목은 편 하나다 (`trip_type: 편도`, 여정 1개). 국내선에만 있는 필드: `direction`, `seat_class`(`특가석`·`특가석-환불불가`·
  `특가석-수하물유료`·`할인석`·`일반석`·`비즈니스석`), `seats_left`(남은 좌석). `baggage_included` 는 수하물 유료 특가석만 `false` 이고 나머지는 모름(null).
- 가격은 편마다 따로다. 왕복 비용은 고른 두 편의 합이고, 두 편 `id` 를 함께 `flight` 에 넣으면 왕복으로 파는 판매처와 가격이 나온다.
- 공항 코드로 찾는다. 공항이 하나뿐인 도시(`CJU`, `PUS`, 이름 `제주`)는 그 공항으로 바꾸고, 공항이 여럿인 도시(`SEL`)는 `INVALID_ARGUMENT` 다 (서울은 `GMP`).
- 좌석은 `--cabin 일반석`(특가·할인·일반석) 또는 `비즈니스석` 만 된다. `--direct` 는 의미가 없다 (모두 직항).
- `total_price_krw` 는 성인·소아 1인 운임 × 인원의 합이다. 유아 운임은 네이버가 계산에 넣지 않아 유아가 있으면 `null` 이다.

```bash
baenggipyo naver search flights GMP 제주 --depart 2026-11-15 --return 2026-11-17
baenggipyo naver flight <가는 편 id> <오는 편 id>
```

## 3. `flight` — 판매처별 가격

`search flights` 의 `id` 를 그대로 넣는다. ID 에 검색 조건이 들어 있어 다른 명령 실행이 끼어도 된다.

| 인자·옵션 | 넣을 수 있는 값 |
| --- | --- |
| `<id>…` | `search flights` 결과의 `id`. 국제선은 하나, 국내선은 편도 검색의 편 하나 또는 왕복 검색의 가는 편·오는 편 둘 (왕복 검색의 편 하나만 주면 `INVALID_ARGUMENT`) |
| `--limit` | 판매처 운임 수 (1~100, 기본 10) |
| `--offer-urls` | 예약 링크를 붙일 앞쪽 판매처 운임 수 (0 이상, 기본 1. [응답 줄이기](common.md#응답-줄이기)) |

반환: 항공권 필드(대표 가격·카드 할인 포함) + `offers[]` — `seller`, `seller_type`(`항공사`·`여행사`), `total_price_krw`, `price_per_adult_krw`,
`fare_name`(예: `성인/모든 결제수단`, `성인/현대카드`), `payment_condition`(결제수단 조건이 있을 때만), `baggage_included`, `url`(네이버를 거쳐 판매처
예약 페이지로 가는 링크).

- `offers` 는 모든 결제수단 운임을 싼 순으로 먼저, 결제수단 조건이 있는 운임을 싼 순으로 뒤에 둔다. `seller_count` 는 `offers` 에 낸 판매처 수다.
  판매처마다 운임(모든 결제수단·카드)이 여럿이라 운임 수보다 적고, 운임이 `--limit` 보다 많으면 `notes` 로 판매처 수와 운임 수를 함께 알린다
  (`판매처 21곳의 운임 114개 중 앞의 10개`).
- 사이트처럼 실시간 가격을 주는 판매처만 낸다. 그래서 `seller_count` 가 `search flights` 의 `seller_count`(실시간 여부를 모르는 검색 결과의 판매처 수)보다
  작을 수 있고, 뺀 판매처 수를 `notes` 로 알린다.
- 대표 가격(`price_per_adult_krw`, `total_price_krw`)·`cheapest_seller`·카드 할인은 `offers` 에 낸 판매처 운임으로 정한다. 그래서 대표 가격으로 살 판매처가
  늘 `offers` 에 있고, 뺀 판매처가 가장 쌌으면 `search flights` 의 가격보다 높을 수 있다.
- 다시 묻는 검색이 시간 상한에 끊긴 채 항공권을 찾았으면 응답한 판매처의 운임만 담겨 있다. 그대로 내되 `notes` 로 알린다 (더 싼 판매처 운임이 빠졌을 수 있다. 국내선도 같다).
- 네이버 검색 결과는 검색 뒤 약 15분 남는다. 지났으면 같은 조건으로 다시 검색하고 `notes` 로 알린다 (가격이 바뀌었을 수 있다).
  다시 찾아도 없으면 `FARE_UNAVAILABLE` 이다. 다시 묻는 검색이 시간 상한에 끊겨 확인하지 못했으면 매진이 아니라 `UPSTREAM_ERROR`
  (`reason: SEARCH_INCOMPLETE`)이니 후보에서 빼지 말고 잠시 뒤 다시 부른다 (국내선도 같다).
- 판매처 조회가 판매처를 하나도 돌려주지 않으면 빈 `offers` 대신 `UPSTREAM_ERROR` 다. 파는 곳이 없는 것이 아니라 네이버 API 가 바뀐 것이니 스카이스캐너로 찾는다.
- **국내선**은 `flights[]`(고른 편, 방금 다시 조회한 값), `total_price_krw`(고른 편 대표 가격의 합), `seller_count`, `offers[]` 를 준다. `offers` 는
  싼 순이다. 고른 편 전부를 한 판매처에서 사는 운임(`fare_name: 성인/모든 결제수단`)과, 왕복에서 두 편의 최저가 판매처가 다를 때 편마다 따로 사는 조합
  (`fare_name: 가는 편만 …`/`오는 편만 …`, 줄마다 그 편 가격)을 조합 합계로 함께 세운다 (같은 값이면 한 판매처 운임이 앞). 조합의 두 줄은 함께 예약해야 하는 운임 하나라 `--limit`·`seller_count` 에서 하나로 세고 가르지 않는다. 유아가 있으면 국내선 `total_price_krw`(항공권·`offers` 모두)는 `null` 이다 (네이버가 유아
  운임을 주지 않는다). `price_per_adult_krw` 는 그대로 성인 1인 요금이고, 합계를 모를 때 `offers` 는 이 값으로 싼 순이다.

## 4. `calendar` — 날짜별 최저가

| 인자·옵션 | 넣을 수 있는 값 |
| --- | --- |
| `<출발>`, `<도착>` | IATA 공항·도시 코드 또는 이름 |
| `--month` | `YYYY-MM` (기본 이번 달) |
| `--trip-days` | 왕복 여행 일수. 가는 날과 오는 날을 모두 센다 (2박 3일 = 3). 주면 출발일마다 그 일수의 왕복 최저가 |
| `--direct` | 직항만 |

반환: `origin`, `destination`, `trip_type`, `trip_days`(왕복만), `month`, `direct`(`--direct` 로 찾았는지), `returned`, `cheapest_days[]`(싼 순 3일),
`days[]`(날짜 순) — `date`, `return_date`(왕복만), `min_price_krw`. 값이 있는 날만 온다. 날마다 직항인지는 네이버가 주지 않는다.

- **실시간 가격이 아니다.** 네이버가 최근 검색에서 모은 최저가이고 카드 할인가가 섞인다. 고른 날은 `search flights` 로 확인한다.

```bash
baenggipyo naver calendar ICN 도쿄 --month 2026-11
baenggipyo naver calendar SEL OSA --month 2026-12 --trip-days 4
```

## 5. `explore` — 어디든지

출발지만 주고 국제선 목적지별 최저가를 싼 순으로 찾는다.

| 인자·옵션 | 넣을 수 있는 값 |
| --- | --- |
| `<출발>` | IATA 공항·도시 코드 또는 이름 |
| `--month` | 출발 달 `YYYY-MM` (주지 않으면 네이버 기본 범위) |
| `--trip-days` | 왕복 여행 일수. 주지 않으면 편도 |
| `--direct` | 직항만 |
| `--limit` | 목적지 수 (1~100, 기본 20) |

반환: `origin`, `trip_type`, `month`, `trip_days`(왕복만), `returned`, `destinations[]` — `code`(목적지 공항 IATA 코드), `city`, `country_code`,
`min_price_krw`, `direct`, `departure_date`, `return_date`(왕복만). `country`(나라 이름)는 네이버가 주지 않아 `null` 이다.

- 실시간이 아닌 추정가다 (`calendar` 와 같다).
- 네이버는 인기 목적지 100곳을 주고, 그중 싼 순으로 자른다. 최저가를 주지 않은 목적지는 빼고 그 수를 `notes` 로 알린다.

```bash
baenggipyo naver explore 서울 --month 2026-11 --trip-days 4
```
