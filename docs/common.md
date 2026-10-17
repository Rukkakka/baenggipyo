# 공통 CLI 사용 규칙

[설치](../README.md) · [LLM 가이드](llm.md) · [네이버](naver.md) · [스카이스캐너](skyscanner.md)


## 출력 규칙

- 결과는 stdout 에 JSON. 터미널이면 들여쓰기, 파이프면 한 줄 (`--pretty` / `--compact` 로 강제)
- `--help` 도 터미널이면 상자로 꾸미고, 파이프면 꾸밈 없는 평문으로 쓴다 (같은 내용이 절반 아래로 짧다)
- 필드에 값이 없을 때는 의미에 따라 다르게 나타낸다

  | 상태 | 표현 | 예 |
  |---|---|---|
  | 해당 없음 (그 항공권·조회에 원래 없는 항목) | **키를 뺀다** | 코드셰어가 아닌 편의 `operating_airline`, 편도 달력의 `return_date` |
  | 모름 (해당은 되지만 소스가 알려 주지 않음) | **`null`** | 기종을 알려 주지 않은 편의 `aircraft`, 수하물 정보가 없는 운임의 `baggage_included` |
  | 알려 줬는데 비어 있음 | `[]` | 직항의 `stopover_airports` |
- 공항이 모호하거나, 검색이 끝나기 전에 시간 상한·요청 제한에 닿았거나, 조건에 맞는 항공권이 `--limit` 보다 많으면 결과의 `notice` 아래에 `notes` 로 안내한다. 안내할 게 없으면 키가 없다
- 에러는 **항상** stderr 에 `{"error": {"code", "message", "hint", ...}}` JSON 으로 쓴다. 사용법 오류(없는 옵션·잘못된 값·인자 누락)도 마찬가지
- **금액은 소스와 무관하게 전부 원 정수**(`*_krw`)다. 총액(`total_price_krw`)과 성인 1인 요금(`price_per_adult_krw`)은 이름으로 구분한다
- **시각은 각 공항의 현지 시각** `YYYY-MM-DDTHH:MM`, 소요 시간은 분(`duration_minutes`). 날짜 입력은 `YYYY-MM-DD`
- 공항·도시는 IATA 코드(`ICN`, 도시 `TYO`), 항공사는 IATA 2자리 코드(`KE`)와 이름

| exit code | 뜻 | 출력 |
|---|---|---|
| 0 | 성공 (`--help`, 인자 없이 실행 포함) | stdout 에 결과 JSON (도움말·`baenggipyo guide`·`baenggipyo --version` 은 텍스트) |
| 1 | 실행 중 에러 (`AIRPORT_NOT_FOUND`, `AIRPORT_AMBIGUOUS`, `ROUTE_NOT_SUPPORTED`, `FARE_UNAVAILABLE`, `NOT_FOUND`, `RATE_LIMITED`, `UPSTREAM_ERROR`, `NETWORK_ERROR`, `PARSE_ERROR`, `INTERNAL_ERROR`, `CLI_ERROR`) | stderr 에 에러 JSON |
| 2 | 입력 오류 (`INVALID_ARGUMENT`) | stderr 에 에러 JSON |
| 130 | 인터럽트로 중단 (`INTERRUPTED`, Ctrl+C·SIGINT) | stderr 에 에러 JSON |

## 반환값을 읽을 때

`id`는 해당 소스의 `flight`에 그대로 넘긴다. 검색 결과 ID 는 시간이 지나면 만료될 수 있다. 금액은 모두 원이고 조회 시점 값이라 예약 단계에서 바뀔 수 있다. `notes`는 조회 범위·제한·조건 해석을 설명하므로 결과와 함께 읽는다. `null`을 0이나 “없음”으로 해석하지 않는다.

목록은 항공권을 고르는 데 필요한 여정(편명·시각·경유)·좌석 등급·대표 가격을, 상세(`flight`)는 판매처별 가격과 예약 링크를 준다. 예약·결제는 `url`의 원 사이트에서 한다. baenggipyo 는 승객 정보를 받지 않는다.

### 공통 항공권 필드

| 필드 | 의미 |
| --- | --- |
| `source`, `id`, `url` | 소스·항공권 ID·원 사이트 링크 |
| `trip_type`, `cabin` | `편도`·`왕복`·`다구간`, 좌석 등급 |
| `journeys[]` | 한 방향 여정마다 출발·도착 공항과 시각, `duration_minutes`, `stops`, `stopover_airports`, `segments[]` |
| `segments[]` | 비행 한 번: `airline_code`, `airline`, `flight_number`, `operating_airline`(코드셰어만), 공항·시각, `duration_minutes`, `aircraft` |
| `total_price_krw`, `price_per_adult_krw` | 대표 가격: 모든 승객 합계, 성인 1인 (소스가 주지 않으면 null) |
| `offer_count`, `cheapest_seller`, `baggage_included` | 판매처 수, 대표 가격의 판매처, 위탁 수하물 포함 여부 |
| 상세 `offers[]` | 판매처마다 `seller`, `seller_type`, `total_price_krw`, `price_per_adult_krw`, `fare_name`, `baggage_included`, 예약 `url` |
| 공항 후보 (`search airports`) | `code`, `type`(`공항`·`도시`), `name`, `city`, `country`, `airports`(도시만), `domestic` |
| 달력 (`calendar` 의 `days[]`) | `date`, `return_date`(왕복만), `min_price_krw`(추정가), `direct` |
| 어디든지 (`explore` 의 `destinations[]`) | `code`, `city`, `country`, `country_code`, `min_price_krw`(추정가), `direct`, `departure_date`, `return_date`(왕복만) |

소스에만 있는 필드(네이버 `card_discount_*` 등)는 소스 문서에 있다.

필드 정의와 확정 상태는 개발 문서의 [출력 계약](../references/output-contract.md#항공권-출력)을 따른다.

### 응답 줄이기

두 소스 모두 같은 옵션으로 응답을 줄인다. LLM 에이전트는 줄인 만큼 토큰을 덜 쓴다.

| 옵션 | 명령 | 하는 일 |
| --- | --- | --- |
| `--brief` | `search flights` | 결과 모양은 그대로 두고 고르는 데 필요 없는 키를 뺀다 (아래) |
| `--offer-urls N` | `flight` | 판매처 예약 링크를 앞의 운임 N개에만 붙인다. 뒤 운임은 `url` 키가 없다. `0` 이면 링크 없이 가격만 |

`--brief` 가 빼는 키:

- 항공권의 `source`·`trip_type`·`cabin`(결과의 `search` 에 있다)과 `url`(`search.url` 이나 `flight <id>` 로 연다)
- 비행 구간(`segments[]`)의 공항·시각·소요 시간·기종·항공사 코드. `airline`·`flight_number`, 코드셰어면 `operating_airline` 만 남는다
  (직항이면 공항·시각이 여정과 똑같고, 경유편은 `stopover_airports` 로 경유지를 본다). 편명이 `null` 이면 `airline_code` 를, 운항 항공사 이름이
  `null` 이면 `operating_airline_code` 를 남긴다
- 네이버 국내선 편의 `direction` (`legs[].direction` 에 있다)

뺀 값(구간별 시각으로 보는 경유 대기 시간, 기종 등)이 필요하면 `--brief` 없이 다시 찾는다. 2026-10-15 인천→간사이 편도 10건이 9,027자에서
5,359자로 줄었다.

`--offer-urls` 는 `--limit` 처럼 운임으로 센다. 판매처 여러 곳에서 따로 예약하는 조합은 줄이 여럿이어도 운임 하나라 줄마다 링크가 갈리지 않는다.
뺀 링크는 모름(`null`)이 아니라 키가 없고, 그 판매처는 상세 맨 위 `url`(원 사이트의 그 항공권이나 검색 결과)에서 고르게 안내한다. 링크 하나가
수백~천 자라, 2026-10-15 네이버 상세(판매처 운임 8개)가 `--offer-urls 1` 로 9,295자에서 2,888자로 줄었다.

## 에러 대응

에러의 `code`와 `source`, `hint`를 읽는다.

| 코드 | 발생 조건 | 대응 |
| --- | --- | --- |
| `INVALID_ARGUMENT` | 필수 인자 누락, 범위·허용값 위반, 지원하지 않는 옵션 조합, 지난 날짜 | 해당 명령의 `--help`와 옵션 표 확인 |
| `AIRPORT_NOT_FOUND` | 이름이나 코드에 해당하는 공항·도시를 찾지 못함 | `search airports` 로 IATA 코드 확인 |
| `AIRPORT_AMBIGUOUS` | 같은 이름의 공항·도시 후보가 여러 곳 | `candidates`에서 코드를 골라 다시 검색 |
| `ROUTE_NOT_SUPPORTED` | 소스가 그 노선을 다루지 않음 (네이버의 국내선 다구간, 국내·국제가 섞인 노선) | `hint`의 다른 소스 사용 |
| `NOT_FOUND` | 조회 대상 없음 | 이전 검색 결과의 ID와 코드 확인 |
| `FARE_UNAVAILABLE` | 매진·판매 중지, 검색 결과 만료 | 해당 항공권 제외 후 다시 검색 |
| `RATE_LIMITED` | 로컬 요청 예산 초과 또는 429 이후 쿨다운 | `retry_after_seconds`가 있으면 해당 시간만큼 대기. 없으면 `hint`와 `baenggipyo limits` 확인 |
| `NETWORK_ERROR` | 연결·타임아웃 등 전송 실패 | 네트워크 상태 확인 후 재시도 |
| `UPSTREAM_ERROR`, `PARSE_ERROR` | API 거절·응답 구조 변경·봇 차단 페이지·파싱 실패 | 힌트 확인, 다른 소스 사용 또는 버전 갱신 |
| `INTERNAL_ERROR` | 처리하지 못한 실행 오류. 출력 인코딩이 UTF-8 이 아니라 결과를 쓰지 못한 경우도 (Windows) | 에러 내용을 보존해 원인 확인. 출력 인코딩 오류면 hint 대로 `PYTHONUTF8=1` 을 설정하고 다시 실행 |
| `CLI_ERROR` | 사용법 오류가 아닌 CLI 처리 오류 (지금 명령에서는 나오지 않음) | 에러 내용을 보존해 원인 확인 |
| `INTERRUPTED` | Ctrl+C 등 호출 중단 | 필요하면 같은 명령을 다시 실행 |

## 요청 제한과 `limits`

모든 baenggipyo 프로세스가 소스별 상태 파일(`~/.cache/baenggipyo/ratelimit/<source>.json`, `BAENGGIPYO_STATE_DIR` 로 변경. 절대 경로가 아니면 무시한다)을 공유한다. 그래서 LLM 이 명령을 연달아 실행해도 간격이 지켜진다.

| 소스 | 요청 간격 | 분당 상한 | 시간당 상한 | 429·봇 차단 후 쿨다운 |
|---|---|---|---|---|
| 네이버 | 1초 | 30 | 없음 | 15분 |
| 스카이스캐너 | 3초 | 12 | 30 | 30분, 거듭 막히면 두 배씩 (최대 4시간) |

네이버는 한계를 재지 않은 값이다. 스카이스캐너는 봇 차단이 몇 시간씩 이어지기도 해 시간당 상한을 두었지만 **막히지 않는다는 보장은 아니다.**
`search flights` 한 번이 자동완성·검색·폴링으로 보통 4~7회(큰 검색은 10회까지), `flight` 가 2~5회 든다.

- 슬롯이 날 때까지 기다리되, 60초 넘게 기다려야 하면 기다리지 않고 `RATE_LIMITED` (`reason: budget`) 를 낸다. 시간당 상한에 닿으면 대개 이 경우다.
  스카이스캐너 검색이 결과를 다시 묻는 도중에 걸리면 에러 대신 받은 데까지 돌려주고 `notes` 로 알린다 (`flight` 는 그 항공권을 아직 못 받았을 때만 `RATE_LIMITED`)
- 429 를 받으면 쿨다운 동안 그 소스로 요청을 아예 보내지 않고 `RATE_LIMITED` (`reason: cooldown`, `retry_after_seconds`) 를 낸다. `Retry-After` 가 더 길면 그 값을 따른다 (4시간까지)
- 스카이스캐너의 봇 차단(403 캡차)도 같은 쿨다운을 걸고 `RATE_LIMITED` (`reason: blocked`) 를 낸다. 쿨다운이 끝나고 4시간 안에 다시 막히면 쿨다운을
  두 배로 늘린다 (차단 중에 쿨다운마다 다시 확인하는 요청을 줄인다). baenggipyo 는 차단을 우회하지 않는다
- `baenggipyo limits` 로 상태를 보고, `baenggipyo limits --reset naver` 로 초기화한다

```bash
baenggipyo limits
baenggipyo limits --compact
baenggipyo limits --reset skyscanner
```

`limits`는 네트워크 호출 없이 상태를 확인한다. `--reset`은 `naver`, `skyscanner`를 반복 또는 쉼표로 받으며 선택한 소스의 로컬 상태를 초기화한다. 서버 차단을 해제하는 명령은 아니다.

| 반환 필드 | 의미 |
| --- | --- |
| `state_dir` | 상태 파일 디렉터리 |
| `sources[].source` | 소스 이름 |
| `sources[].requests_last_minute` | 최근 60초 요청 슬롯 수 |
| `sources[].requests_last_hour` | 최근 1시간 요청 슬롯 수. 시간당 상한이 있는 소스(스카이스캐너)만 키가 있다 |
| `sources[].cooldown_remaining_seconds` | 남은 쿨다운 시간 |
| `sources[].policy` | 간격·분당 상한·시간당 상한(`max_per_hour`)·쿨다운·쿨다운 상한(`max_cooldown`)·최대 대기·활성화 정책 |

상태 파일 권한·디스크 오류도 stderr 에러 JSON으로 반환한다. `--pretty` / `--compact`는 소스별 명령과 `limits`에서 동일하게 사용할 수 있다. 전역 `--verbose`는 진행 로그를 stderr에 추가하므로 에러 JSON만 파싱하는 호출에서는 사용하지 않는다.
