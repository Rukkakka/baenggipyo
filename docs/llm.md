# baenggipyo LLM 사용 가이드

셸로 `baenggipyo` 를 불러 항공권을 찾는 LLM 에이전트용 요약이다. `baenggipyo guide` 는 공통 부분을, `baenggipyo guide naver`·`baenggipyo guide skyscanner` 는 그 소스만의 주의점을 출력한다. 쓸 소스의 주의점도 함께 읽는다. 옵션은 `baenggipyo <소스> <명령> --help` 로 본다.

## 꼭 지킬 규칙

- 결과는 stdout 에 JSON, 에러는 stderr 에 `{"error": {"code", "message", "hint", ...}}` 다 (exit 0 성공, 1 실행 에러, 2 입력 오류).
- 금액은 모두 **원 정수**(`*_krw`)다. 총액(`total_price_krw`, 모든 승객·세금 포함)과 성인 1인 요금(`price_per_adult_krw`)을 섞어 말하지 않는다.
- 시각은 **각 공항의 현지 시각**이고 도착 날짜가 출발 날짜와 다를 수 있다. 비행 시간은 시각끼리 빼지 말고 `duration_minutes` 를 쓴다.
- 출발·도착은 IATA 코드(`ICN`, 도시 `TYO`)나 한글 이름(`도쿄`)으로 바로 넣는다. `search airports` 를 먼저 부르지 않는다. 도시 코드는 그 도시의 모든 공항을 찾는다. 대문자 4글자(`CJUA`, `RKSI`, `JEJU`)는 받지 않는다.
- 값이 없을 때: **키 없음** = 해당 없음, **`null`** = 모름 (0·없음이 아니다), **`[]`** = 비어 있음. `baggage_included` 가 `null` 이면 "수하물 미포함"이라고 말하지 않는다.
- `notes` 는 꼭 읽는다. 일부만 돌려줬는지, 검색이 덜 끝났는지, 요청이 얼마 남지 않았는지를 알려 준다.
- 가격은 조회 시점 값이라 예약 단계에서 바뀔 수 있다고 알린다. baenggipyo 는 예약·결제를 하지 않으니 결과의 `url` 로 안내한다.
- `id` 는 받은 그대로 같은 소스의 `flight` 에 넣는다. 소스끼리 통하지 않는다.
- 명령은 하나씩 차례로 부른다. `--verbose` 는 쓰지 않는다 (stderr 에 로그가 섞인다).
- 결과 JSON 이 여러 줄로 들여쓰기돼 오면 `BAENGGIPYO_PLAIN=1 baenggipyo …` 로 부른다. 한 줄 JSON 이면 붙이지 않아도 된다.

## 명령

`<소스>` 는 `naver` 또는 `skyscanner` 다. 국내선은 네이버가 가볍고 빠르다.

- 편도·왕복: `<소스> search flights <출발> <도착> --depart YYYY-MM-DD [--return YYYY-MM-DD]`. 다구간은 `--leg ICN-NRT-2026-11-01 --leg KIX-ICN-2026-11-05`
- 판매처별 가격·예약 링크: `<소스> flight <id>`
- 날짜별 최저가(추정가): `<소스> calendar <출발> <도착> --month YYYY-MM` (왕복은 네이버 `--trip-days N`)
- 목적지 미정(어디든지, 추정가): `<소스> explore <출발> --month YYYY-MM`
- 공항 후보: `<소스> search airports <이름>` (후보를 사용자에게 보여 줄 때만)
- 요청 제한 상태: `baenggipyo limits` (요청 없음)

날짜가 미정이면 `calendar`, 목적지가 미정이면 `explore` 로 고른 뒤 `search flights` → `flight <id>` 로 간다. `calendar`·`explore` 가격은 최근 검색에서 모은 추정가라 `search flights` 로 실제 가격을 확인한다.

## 응답 줄이기

결과 JSON 은 그대로 대화에 쌓인다. 필요한 만큼만 받는다.

- `search flights` 는 기본이 요약이다. 경유 대기 시간·기종이 필요할 때만 `--full` 을 붙인다. `--limit` 은 보여 줄 만큼만.
- "오전 출발"·"저녁에 돌아오는"처럼 시각대가 정해졌으면 `--depart-time 06-12`·`--return-time 18-24` 를 쓴다 (경계는 0·6·9·12·15·18·21·24시).
- 날짜가 미정이면 여러 날을 `search flights` 하지 말고 `calendar` 의 `cheapest_days`(싼 순 3일)부터 본다.
- `flight <id>` 는 기본으로 맨 앞(최저가) 운임에만 예약 링크를 붙인다. 링크 하나가 수백~2천 자라 더 필요할 때만 `--offer-urls N`, 가격만 비교하려면 `--offer-urls 0` 을 쓴다. 네이버 국제선은 맨 위 `url`(200자쯤)이 그 항공권을 고른 화면이라 사용자에게 링크 하나만 주면 될 때도 `--offer-urls 0` 으로 충분하다. 링크를 뺀 운임은 `url` 키가 없고(`notes` 에 알림) 링크가 없는 판매처가 아니니, 그 판매처는 맨 위 `url`(원 사이트)에서 고르게 안내한다.

## 에러가 나면

`hint` 를 먼저 읽는다.

| code | 할 일 |
| --- | --- |
| `INVALID_ARGUMENT` | `message` 대로 옵션을 고친다. 같은 명령을 그대로 다시 부르지 않는다 |
| `AIRPORT_NOT_FOUND`, `AIRPORT_AMBIGUOUS` | 한글 도시 이름이나 `candidates` 의 IATA 코드로 다시 부른다 |
| `ROUTE_NOT_SUPPORTED` | `hint` 의 다른 소스로 찾는다 |
| `NOT_FOUND`, `FARE_UNAVAILABLE` | ID 를 그대로 복사했는지 본다. 매진·만료면 후보에서 빼고 `search flights` 로 다시 찾는다 |
| `RATE_LIMITED` | 바로 다시 부르지 않는다. `retry_after_seconds` 만큼 기다리거나 다른 소스를 쓴다 |
| `NETWORK_ERROR` | 잠시 뒤 한 번만 다시 부른다 |
| `UPSTREAM_ERROR`, `PARSE_ERROR` | 다른 소스를 쓰고 업데이트(`uv tool upgrade baenggipyo`)를 권한다. `reason: SEARCH_INCOMPLETE` 는 잠시 뒤 한 번 다시 부른다 |
| `INTERNAL_ERROR`, `CLI_ERROR` | 에러 JSON 을 `baenggipyo --version` 과 함께 사용자에게 보여 준다. `hint` 가 `PYTHONUTF8=1` 이면 붙여 한 번 다시 부른다 |

더 자세한 설명: [공통 규칙](https://github.com/Rukkakka/baenggipyo/blob/main/docs/common.md) · [네이버](https://github.com/Rukkakka/baenggipyo/blob/main/docs/naver.md) · [스카이스캐너](https://github.com/Rukkakka/baenggipyo/blob/main/docs/skyscanner.md)

## 네이버

`baenggipyo guide naver` 가 이 절을 출력한다.

- **국내선 왕복은 결과 모양이 다르다.** `itineraries` 대신 `legs[]`(가는 편·오는 편)에서 하나씩 고르고, 가격은 편마다 따로다. 두 편의 `id` 를 함께 넣으면(`flight <가는 편 id> <오는 편 id>`) 왕복 예약 판매처와 가격이 나온다. 국내선 편도는 국제선과 같은 `itineraries` 다.
- 국내선은 공항 코드로 찾는다 (서울은 `GMP`). 국내선 다구간과 국내·국제가 섞인 노선은 `ROUTE_NOT_SUPPORTED` 다.
- `search flights` 는 10~20초 걸린다. 같은 검색을 되풀이하지 않는다.
- 대표 가격은 어떤 결제수단으로도 받는 운임의 최저가다. `card_discount_price_krw` 는 특정 카드·결제수단으로만(실적 조건이 붙기도 한다) 받는 성인 1인 가격이라, 대표 가격처럼 말하지 말고 `card_discount_name` 과 함께 알린다.
- `flight` 의 `offers` 는 모든 결제수단 운임이 먼저, 카드 조건 운임이 뒤다. 검색 뒤 15분이 지났으면 다시 검색하고 `notes` 로 알린다.
- `--trip-days` 는 가는 날과 오는 날을 모두 센다 (3박 4일 = 4).

## 스카이스캐너

`baenggipyo guide skyscanner` 가 이 절을 출력한다.

- **봇 차단으로 몇 시간씩 막힐 수 있다.** 막히면 `RATE_LIMITED`(`reason: blocked`·`cooldown`), 시간당 30회 상한에 닿아도 `RATE_LIMITED`(`reason: budget`)다. 어느 쪽이든 기다리지 말고 네이버로 같은 조회를 한다. 네이버로 안 되는 것(국내선 다구간, 국내·국제가 섞인 노선)이나 비교가 꼭 필요할 때만 부른다. 검색 한 번에 4~7회 든다.
- 가격은 **모든 승객 합계**뿐이라 성인이 2명 이상이면 `price_per_adult_krw` 가 null 이다. 나눠서 1인 요금이라고 말하지 않는다.
- 직항·항공사 조건과 정렬은 받은 결과에서 baenggipyo 가 한다. 국내선 왕복은 결과가 수천 건이라 느리다.
- `flight <id>` 는 같은 조건으로 다시 검색해 찾는다 (가격이 바뀌었을 수 있다).
- `self_transfer: true` 는 표를 따로 사서 갈아타는 셀프 환승이다. 연결편을 놓쳐도 보호받지 못하고 짐을 다시 부쳐야 할 수 있으니, 일반 경유편처럼 말하지 말고 이 위험을 함께 알린다.
- `calendar` 는 편도만 된다. `explore` 는 목적지 IATA 코드를 주지 않고(`code: null`) `sky_code`(`CJUA`)는 검색에 쓸 수 없으니 `city` 이름으로 찾는다. `explore` 가격은 그 기간 어느 날의 최저가라 `calendar` 로 그날을 찾은 뒤 `search flights` 한다.
