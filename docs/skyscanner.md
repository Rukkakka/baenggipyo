# 스카이스캐너 CLI 사용법 (`baenggipyo skyscanner`)

[설치](install.md) · [공통 사용 규칙](common.md) · [개발 가이드](../references/providers/skyscanner.md)

> 금액은 **원**, 시각은 **각 공항 현지 시각**이다. 출력 형식·누락값·에러 대응·요청 제한 같은 공통 규칙은
> [공통 사용 규칙](common.md)에 있고, 이 문서는 스카이스캐너에만 있는 내용을 적는다.
>
> 국내선도 찾는다. 국내선은 네이버가 더 가볍고 빠르다. 스카이스캐너는 봇 차단이 있어 몇 시간씩 막힐 수 있다. 막히면 30분(거듭되면 최대 4시간) 동안 요청을 보내지 않고 `RATE_LIMITED` 를 내며,
> 시간당 30회까지만 보낸다 ([요청 제한](common.md#요청-제한과-limits)). 막히면 네이버로 찾는다.

## 한눈에 보기

| 명령 | 하는 일 | 무엇을 넣나 | 요청 수 | 다음에 쓰는 곳 |
| --- | --- | --- | --- | --- |
| `search airports` | 공항·도시 IATA 코드 찾기 | 이름 또는 코드 | 1 | `search flights`, `calendar`, `explore` |
| `search flights` | 편도·왕복·다구간 항공권 | 출발·도착과 날짜 | 공항 입력마다 1 + 검색 1 + 폴링 (보통 5~15초) | `flight` |
| `flight` | 항공권 하나의 판매처별 가격·예약 링크 | 항공권 ID | 검색 1 + 폴링 | — |
| `calendar` | 한 달의 편도 날짜별 최저가 (추정가) | 출발·도착과 달 | 3 | `search flights` |
| `explore` | 어디든지: 도시별 최저가 (추정가) | 출발지와 달 | 2 | `calendar`, `search flights` |

### 보통 이렇게 이어서 쓴다

```
항공권 찾기   search airports ─▶ search flights ─▶ flight
날짜 고르기   calendar ─▶ search flights --depart <싼 날> ─▶ flight
목적지 고르기 explore ─▶ calendar / search flights (도시 이름으로)
```

### 출발지·도착지 넣는 법

[네이버와 같다](naver.md#출발지도착지-넣는-법): 3글자 코드는 같은 코드의 공항을 먼저, 없으면 도시를 고르고, 이름은 이름이 똑같은 도시·공항을
고른다. 대문자 4글자 코드(`explore` 의 `sky_code` `CJUA` 등)는 요청 없이 거르니 한글 도시 이름으로 넣는다. 입력마다 요청이 1회 든다.

---

## 1. `search airports` — 공항·도시 코드 찾기

| 인자·옵션 | 넣을 수 있는 값 |
| --- | --- |
| `<이름>` | 공항·도시 이름 (한국어·영어) 또는 코드 |
| `--limit` | 후보 수 (1~20, 기본 10) |

반환: `query`, `returned`, `airports[]` — `code`, `type`, `name`, `city`, `country`, `airports`(스카이스캐너가 주지 않아 도시도 null), `domestic`.
IATA 코드를 주지 않은 도시는 `code` 가 null 이다 (스카이스캐너 도시 코드는 4글자라 검색에 쓸 수 없다). 그 도시는 이름으로 넣는다.

## 2. `search flights` — 항공권 찾기

옵션은 [네이버 `search flights`](naver.md#2-search-flights--항공권-찾기)와 같다 (`--depart`·`--return`·`--leg`, 승객, `--cabin`, `--direct`,
`--airline`, `--depart-time`·`--return-time`, `--sort`, `--limit` 1~50·기본 10, `--brief`(기본)/`--full`). 다른 점:

- **가격은 모든 승객 합계**(`total_price_krw`)다. 1인 요금을 주지 않아 `price_per_adult_krw` 는 성인 1명만 찾을 때만 값이 있고(합계와 같다) 그 밖에는
  `null` 이다.
- 소아는 만 8세, 유아는 만 1세로 보내 찾는다 (스카이스캐너는 나이로 받는다).
- 스카이스캐너는 결과를 한 번에 다 준다. **직항·항공사·출발 시각대 조건과 정렬은 받은 결과에서 baenggipyo 가 한다** (조건으로 거른 수는 `notes` 로 알린다.
  정렬은 늘 baenggipyo 가 하니 알리지 않는다. 스카이스캐너 사이트의 기본 순서는 추천순이라 `search.url` 로 열면 순서가 다르다). `search.total_count` 는
  조건을 걸기 전 결과 수다. 여정 정보나 조건을 판정할 값(경유 수·판매 항공사)이 응답에 없는 항공권, 판매처 운임이 모두 가격 없음으로 온 항공권은
  빼고 그 수를 `notes` 로 알린다.
- 검색 도중 결과를 다시 묻는 요청이 [요청 제한](common.md#요청-제한과-limits)(시간당 상한·쿨다운)에 걸리면 에러로 끝내지 않고 받은 데까지 돌려주며,
  언제 풀리는지 `notes` 로 알린다.
- `baggage_included` 는 주지 않아 `null` 이다.
- 스카이스캐너 전용 필드 `self_transfer`: 표를 따로 사서 갈아타는 **셀프 환승**이면 `true` 이고, 아니면 키가 없다 (스카이스캐너가 알려 주지 않으면
  `null`). 연결편을 놓쳐도 보호받지 못하고 짐을 다시 부쳐야 할 수 있어 `notes` 로도 알린다. `flight` 결과도 같다.
- **국내선 왕복은 결과가 매우 많다** (김포↔제주 왕복 8,359건, 응답 약 100MB, 10~40초). 가능하면 편도 둘로 나눠 찾는다.

```bash
baenggipyo skyscanner search flights GMP CJU --depart 2026-11-15 --return 2026-11-17
baenggipyo skyscanner search flights ICN NRT --depart 2026-11-20 --direct --airline KE,OZ
```

## 3. `flight` — 판매처별 가격

`search flights` 의 `id` 를 그대로 넣는다 (여러 항공사를 타는 항공권은 ID 에 쉼표가 들어 있다). 같은 조건으로 **다시 검색해** 그 항공권을 찾으므로
가격은 방금 값이다. 검색이 끝났는데 없으면 `FARE_UNAVAILABLE` 이다. 검색이 60초 안에 끝나지 않아 아직 못 받았으면 매진이 아니라 `UPSTREAM_ERROR`
(`reason: SEARCH_INCOMPLETE`)이니 잠시 뒤 다시 부른다. 검색 도중 요청 제한에 걸려 아직 못 받았으면 `RATE_LIMITED` 다. 검색 응답에 세션 ID 나
진행 상태가 없어 더 묻지 못했으면 다시 불러도 같아 `PARSE_ERROR` 다.

| 인자·옵션 | 넣을 수 있는 값 |
| --- | --- |
| `<id>` | `search flights` 결과의 `id` |
| `--limit` | 판매처 운임 수 (1~100, 기본 10) |
| `--offer-urls` | 예약 링크를 붙일 앞쪽 판매처 운임 수 (0 이상, 기본 1. [응답 줄이기](common.md#응답-줄이기)). 링크를 뺀 판매처는 맨 위 `url`(검색 결과 페이지)에서 고른다 |

반환: 항공권 필드 + `offers[]` — `seller`, `seller_type`(`항공사`·`여행사`), `total_price_krw`, `price_per_adult_krw`, `fare_name`(판매처 여러 곳에서 따로
예약하는 조합일 때만), `url`(스카이스캐너를 거쳐 판매처로 가는 예약 링크).

- 스카이스캐너가 가격 없음으로 준 판매처 운임(0원)은 빼고 `notes` 로 알린다. `seller_count` 도 가격을 준 판매처만 센다. 판매처 운임이 모두 가격
  없음이면 `search flights` 처럼 결과에 없는 것으로 보고 `FARE_UNAVAILABLE` 이다.
- `offers` 는 판매처 운임을 싼 순으로 둔다. 판매처 여러 곳에서 따로 예약하는 조합(예: 가는 편은 이스타항공, 오는 편은 티웨이)도 조합 합계로
  함께 세우고(가격이 같으면 한 판매처 운임이 앞), 예약 단위마다 한 줄씩 낸다. 그 줄의 가격·`url` 은 `fare_name` 의 구간(`PUS→FUK만 (나머지는 … 에서 따로 예약)`)만이라
  같은 조합의 줄을 모두 예약해야 한다. 대표 가격이 조합이면 `cheapest_seller` 는 판매처를 ` + ` 로 잇는다.
- `--limit` 은 줄이 아니라 판매처 운임 수다. 조합 하나는 운임 하나로 세고 줄을 가르지 않으므로, 조합이 들어가면 `offers` 줄 수가 `--limit` 보다 많을 수 있다.

## 4. `calendar` — 날짜별 최저가 (편도)

| 인자·옵션 | 넣을 수 있는 값 |
| --- | --- |
| `<출발>`, `<도착>` | IATA 공항·도시 코드 또는 이름 |
| `--month` | `YYYY-MM` (기본 이번 달) |

반환: `origin`, `destination`, `trip_type`(`편도`), `month`, `returned`, `cheapest_days[]`(싼 순 3일), `days[]`(날짜 순) — `date`, `min_price_krw`.
날마다 직항인지는 스카이스캐너가 주지 않는다.

- **편도만 된다.** 왕복·직항 달력은 [네이버](naver.md#4-calendar--날짜별-최저가)를 쓴다.
- 실시간이 아닌 추정가다. 고른 날은 `search flights` 로 확인한다.

## 5. `explore` — 어디든지

출발지만 주고 도시별 지표 최저가를 싼 순으로 찾는다. 국내 도시도 나온다.

| 인자·옵션 | 넣을 수 있는 값 |
| --- | --- |
| `<출발>` | IATA 공항·도시 코드 또는 이름 |
| `--month` | 출발 달 `YYYY-MM` (주지 않으면 언제든) |
| `--round-trip` | 같은 달 안의 왕복 (여행 일수는 정할 수 없다) |
| `--direct` | 직항 지표 가격만 (직항이 없는 도시는 빠지고 그 수를 `notes` 로 알린다) |
| `--limit` | 도시 수 (1~100, 기본 20) |

반환: `origin`, `trip_type`, `month`, `returned`, `destinations[]` — `code`(IATA 코드를 주지 않아 null), `city`, `country`·`country_code`(주면),
`min_price_krw`, `direct`, `departure_date`·`return_date`(주지 않아 null), `sky_code`(스카이스캐너 도시 코드, 예 `CJUA`).

- 지표 가격(추정가)이고 날짜를 주지 않는다. 가격은 그 기간 어느 날의 최저가라 다른 날은 몇 배 비쌀 수 있다 (인천→제주 11월 26,928원은 9·23일 같은
  날의 값이고 15일은 315,865원이었다, 2026-10-20). 고른 도시는 `calendar` 로 그 가격의 날을 찾은 뒤 `search flights` 한다. `--round-trip` 은
  `calendar` 가 편도만 되어 날짜를 찾을 수 없으니 그 달 날짜로 `search flights` 한다.
- `sky_code` 는 IATA 가 아니라 검색에 쓸 수 없다 (`AIRPORT_NOT_FOUND`). 고른 도시는 도시 **이름**(`city`)이나 `search airports` 의 코드로 넣는다.
- 지표 가격이 없는 도시는 싼 순으로 세울 수 없어 빼고 그 수를 `notes` 로 알린다.

```bash
baenggipyo skyscanner explore SEL --month 2026-11 --round-trip
```
