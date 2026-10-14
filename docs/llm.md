# baenggipyo LLM 사용 가이드

셸로 `baenggipyo` 를 불러 항공권을 찾는 LLM 에이전트용 요약이다. `baenggipyo guide` 가 이 문서를 그대로 출력한다. 이 문서 하나로 시작하고, 옵션 세부는 `baenggipyo <소스> <명령> --help`, 더 깊은 설명은 아래 링크한 명령 절만 골라 읽는다.

## 꼭 지킬 규칙

- 결과는 stdout 에 JSON 하나, 에러는 stderr 에 `{"error": {"code", "message", "hint", ...}}`. exit 0 성공, 1 실행 에러, 2 입력 오류, 130 중단.
- 금액은 소스와 상관없이 전부 **원 정수**(`*_krw`)다. 총액(`total_price_krw`, 모든 승객·세금 포함)과 성인 1인 요금(`price_per_adult_krw`)을 섞어 말하지 않는다.
- 시각은 **각 공항의 현지 시각**(`YYYY-MM-DDTHH:MM`)이다. 비행 시간은 출발·도착 시각을 빼서 구하지 말고 `duration_minutes` 를 쓴다. 도착 날짜가 출발 날짜와 다를 수 있다.
- 공항·도시는 IATA 코드로 넣는다 (`ICN`, `NRT`, 도시 코드 `TYO`). 이름도 받지만 여러 곳에 맞으면 `AIRPORT_AMBIGUOUS` 다. 도시 코드는 그 도시의 모든 공항을 찾는다.
- 값이 없을 때: **키 없음** = 해당 없음, **`null`** = 모름 (0·없음이 아니다), **`[]`** = 알려 줬는데 비어 있음. `baggage_included` 가 `null` 이면 "수하물 미포함"이라고 말하지 않는다.
- `notes` 는 꼭 읽는다. 일부만 돌려줬는지, 검색이 끝나기 전에 멈췄는지, 공항이 모호한지를 알려 준다. 맨 앞 `notice` 는 늘 같은 안내라 건너뛰어도 된다.
- 가격은 조회 시점 값이다. 예약 단계에서 바뀔 수 있다고 사용자에게 알린다. baenggipyo 는 예약·결제를 하지 않으니 결과의 `url` 로 안내한다.
- ID 는 받은 그대로 같은 소스 명령에 넣는다. 소스끼리 ID 는 통하지 않는다. 검색 결과 ID 는 시간이 지나면 만료될 수 있다 (`FARE_UNAVAILABLE`).
- 명령은 하나씩 차례로 부른다. 요청 간격은 모든 baenggipyo 프로세스가 함께 지켜서 동시에 불러도 빨라지지 않는다.
- `--verbose` 는 쓰지 않는다 (stderr 에 로그가 섞인다).

## 무엇을 하려면

`<소스>` 는 `naver` 또는 `skyscanner`. 국내선은 네이버가 가볍고 빠르다 (스카이스캐너 국내선 왕복은 수천 건이라 느리다).

| 하려는 일 | 명령 |
| --- | --- |
| 공항·도시 코드 찾기 | `<소스> search airports <이름>` |
| 편도 | `<소스> search flights <출발> <도착> --depart YYYY-MM-DD` |
| 왕복 | `<소스> search flights <출발> <도착> --depart YYYY-MM-DD --return YYYY-MM-DD` |
| 다구간 | `<소스> search flights --leg ICN-NRT-2026-11-01 --leg KIX-ICN-2026-11-05` |
| 항공권 하나의 판매처별 가격·예약 링크 | `<소스> flight <id>` |
| 날짜별 최저가 (추정가) | `<소스> calendar <출발> <도착> --month YYYY-MM` (왕복은 네이버만 `--trip-days N`. 스카이스캐너는 편도만) |
| 목적지 미정 (어디든지, 추정가) | `<소스> explore <출발> --month YYYY-MM` (왕복은 네이버 `--trip-days N`, 스카이스캐너 `--round-trip`) |
| 요청 제한 상태 | `baenggipyo limits` (네트워크 요청 없음) |
| 이 가이드 다시 보기 | `baenggipyo guide` (네트워크 요청 없음) |

## 자주 쓰는 흐름

```text
항공권 찾기   search airports ─▶ search flights ─▶ flight <id>
날짜 고르기   calendar ─▶ search flights --depart <싼 날> ─▶ flight <id>
목적지 고르기 explore ─▶ calendar / search flights
```

## 소스마다 다른 점

- `calendar`·`explore` 가격은 두 소스 모두 실시간이 아니라 최근 검색에서 모은 추정가다. 고른 뒤 `search flights` 로 실제 가격을 확인한다.
- 스카이스캐너 `explore` 의 도시 코드는 IATA 가 아니다 (`CJUA`). `search airports` 로 IATA 코드를 찾아 검색한다.

### 네이버

- **국내선은 결과 모양이 다르다.** `legs[]`(가는 편·오는 편)마다 편 목록이 오고, 왕복은 각 목록에서 하나씩 고른다. 가격은 편마다 따로다. 두 편의 `id` 를 함께 `flight` 에 넣으면 왕복 예약 판매처와 가격이 나온다 (`flight <가는 편 id> <오는 편 id>`).
- 국내선은 공항 코드로 찾는다 (서울은 `GMP`). 국내선 다구간과 국내·국제가 섞인 노선은 `ROUTE_NOT_SUPPORTED` 다.
- `search flights` 는 10~20초 걸린다. 한 번 찾은 결과로 고르고, 같은 검색을 되풀이하지 않는다.
- 대표 가격은 어떤 결제수단으로도 받는 운임의 최저가다. `card_discount_price_krw` 는 특정 카드·결제수단으로만, 실적 조건이 붙기도 하는 성인 1인 가격이다. 대표 가격처럼 말하지 말고 `card_discount_name` 과 함께 알린다.
- `flight <id>` 의 `offers` 는 모든 결제수단 운임이 먼저, 카드 조건 운임이 뒤에 온다. 검색 뒤 15분이 지났으면 자동으로 다시 검색하고 `notes` 로 알린다.
- `--trip-days` 는 가는 날과 오는 날을 모두 센다 (3박 4일 = 4).

### 스카이스캐너

- 국내선도 찾는다. 가격은 **모든 승객 합계**뿐이라 성인이 2명 이상이면 `price_per_adult_krw` 가 null 이다. 나눠서 1인 요금이라고 말하지 않는다.
- 직항·항공사 조건과 정렬은 받은 결과에서 baenggipyo 가 한다. 국내선 왕복은 결과가 많아(수천 건) 느리다.
- `flight <id>` 는 같은 조건으로 다시 검색해 찾는다 (가격이 바뀌었을 수 있다).
- `self_transfer: true` 는 표를 따로 사서 갈아타는 셀프 환승이다. 연결편을 놓쳐도 보호받지 못하고 짐을 다시 부쳐야 할 수 있으니, 추천할 때 일반 경유편처럼 말하지 말고 이 위험을 함께 알린다.
- `calendar` 는 편도만 된다. `explore` 의 `code` 는 IATA 가 아니니 도시 이름으로 다시 찾는다.
- **봇 차단으로 몇 시간씩 막힐 수 있다.** 막히면 30분(거듭되면 최대 4시간) 동안 요청을 보내지 않고 `RATE_LIMITED`(`reason: blocked` 또는 `cooldown`)를 낸다.
  시간당 30회 상한에 닿아도 `RATE_LIMITED`(`reason: budget`)다. 어느 쪽이든 기다리지 말고 네이버로 같은 조회를 한다. 스카이스캐너는 네이버로 안 되는 것
  (국내선 다구간, 국내·국제가 섞인 노선 등)이나 비교가 꼭 필요할 때만 부른다.

## 에러가 나면

| code | 할 일 |
| --- | --- |
| `INVALID_ARGUMENT` | `message` 를 읽고 `--help` 로 옵션을 고친다. 같은 명령을 그대로 다시 부르지 않는다 |
| `AIRPORT_NOT_FOUND` | `search airports` 로 IATA 코드를 찾아 다시 부른다. 없는 3글자 코드면 `hint` 에 비슷한 후보가 있다 (사용자가 뜻한 곳인지 확인하고 쓴다) |
| `AIRPORT_AMBIGUOUS` | `candidates` 에서 코드를 골라 다시 부른다 |
| `ROUTE_NOT_SUPPORTED` | 그 소스가 다루지 않는 노선이다 (네이버 국내선 다구간, 국내·국제 혼합). `hint` 의 다른 소스로 찾는다 |
| `NOT_FOUND` | ID·코드를 이전 결과에서 그대로 복사했는지 확인한다 |
| `FARE_UNAVAILABLE` | 매진됐거나 검색 결과가 만료됐다. 후보에서 빼고 `search flights` 로 다시 찾는다 |
| `RATE_LIMITED` | `retry_after_seconds` 만큼 기다리거나 다른 소스를 쓴다. 바로 다시 부르지 않는다 |
| `NETWORK_ERROR` | 잠시 뒤 한 번만 다시 부른다 |
| `UPSTREAM_ERROR`, `PARSE_ERROR` | 사이트가 바뀌었거나 봇 차단 페이지일 수 있다. 다른 소스를 쓰고, 사용자에게 baenggipyo 업데이트(`uv tool upgrade baenggipyo`)를 권한다. 단 `reason: SEARCH_INCOMPLETE` 는 검색이 늦게 끝난 것이라 후보에서 빼지 말고 잠시 뒤 한 번 다시 부른다 |
| `INTERNAL_ERROR`, `CLI_ERROR` | 에러 JSON 을 `baenggipyo --version` 출력과 함께 그대로 사용자에게 보여 준다. `hint` 가 `PYTHONUTF8=1` 이면 그 환경 변수를 붙여 한 번 다시 부른다 |

## 더 읽을 곳

필요한 절만 읽는다. `baenggipyo guide` 로 읽어도 열 수 있게 전체 주소로 적었다.

- [공통 사용 규칙](https://github.com/Rukkakka/baenggipyo/blob/main/docs/common.md) — 출력 규칙, 공통 항공권 필드, 요청 제한 정책
- [네이버](https://github.com/Rukkakka/baenggipyo/blob/main/docs/naver.md) · [스카이스캐너](https://github.com/Rukkakka/baenggipyo/blob/main/docs/skyscanner.md) — 명령별 옵션 표·반환값·주의사항
