# Changelog

baenggipyo 의 버전별 변경 기록. 사용자(에이전트 포함)에게 보이는 변경만 적고, 아직 릴리스하지 않은 변경은
`Unreleased` 에 쌓는다. 번호 규칙과 쓰는 법은 [references/release.md](references/release.md) 에 있다.

## Unreleased

### 수정

- 서버 오류 본문이 JSON 으로 읽히지 않을 때(HTML·잘린 JSON) 따옴표 친 세션·추적 ID(`"sessionId": "…"`)를 가리지 않고 에러 `message` 와
  `--verbose` 로그에 싣던 문제 (#65)
- 스카이스캐너 검색 응답에 진행 상태(`context.status`)가 없거나 모르는 값이면 시간 상한에 멈췄다고 안내하던 문제. `notes` 에 그 까닭을 적고,
  `flight` 는 `SEARCH_INCOMPLETE`(잠시 뒤 다시) 대신 `PARSE_ERROR`(`reason: SEARCH_STATUS_UNKNOWN`)다. 세션 ID 가 없어 멈춘 `PARSE_ERROR` 에도
  `reason: SESSION_ID_MISSING` 을 붙인다 (#65)
- 스카이스캐너가 운항 항공사 코드 없이 이름만 준 공동운항 구간에서 운항 항공사가 빠져 자사편처럼 보이던 문제. 판매 항공사와 이름이 다르면
  `operating_airline` 과 `operating_airline_code: null` 을 낸다 (#65)
- 스카이스캐너 `flight` 의 판매처 여러 곳 조합 `fare_name` 이 공항 코드를 모르면 `None→None` 으로 나오던 문제 (#65)
- `explore` 가 최저가(스카이스캐너 `--direct` 는 직항 지표 가격)를 주지 않은 목적지를 말없이 빼던 문제. 뺀 수를 `notes` 로 알린다 (#65)

## 0.3.0 — 2026-10-17

### 변경

- 두 소스가 보내는 브라우저 `User-Agent` 의 크롬 판을 131 에서 152 로 올린다 (#61)

### 수정

- 스카이스캐너 `operating_airline_code` 가 운항 항공사 코드를 모를 때 빈 문자열(`""`) 대신 `null` 이다. 자사편이 공동운항처럼 운항 항공사를
  달고 나오던 문제 (#56)
- 서버가 아주 긴 `Retry-After`(먼 날짜 포함)를 주면 `limits --reset` 전까지 그 소스를 못 쓰던 문제. 쿨다운은 그 값을 4시간까지만 따른다 (#56)
- 스카이스캐너 검색 응답에 세션 ID 가 없어 멈췄을 때 시간 상한에 멈췄다고 안내하던 문제. `notes` 에 그 까닭을 적고, `flight` 는
  `SEARCH_INCOMPLETE`(잠시 뒤 다시) 대신 `PARSE_ERROR` 다 (#56)
- 네이버 국내선 왕복 검색이 시간 상한에 끊겼다는 `notes` 에 가는 편·오는 편 중 어느 쪽인지 붙인다 (#56)
- 네이버 국내선 `flight` 가 실시간 가격을 주지 않아 뺀 판매처 수를 국제선처럼 `notes` 로 알린다. 모두 빠져 `offers` 가 비면 그렇다고 적는다 (#56)

## 0.2.1 — 2026-10-17

### 수정

- 네이버 국내선 왕복 `flight` 에서 대표 가격과 같은 판매처 조합(가는 편·오는 편 따로 예약)이 `--limit`·`--offer-urls` 에 잘리던 문제. 이제
  `offers` 를 싼 순으로 두고 조합도 합계로 함께 세운다. 합계를 모르면(유아 등) 성인 1인 요금 순이다 (#40)
- 스카이스캐너 검색 폴링이 실패한 에러 메시지와 `--verbose` 요청 로그에 검색 세션 ID 를 싣지 않는다 (#41)
- 스카이스캐너 검색 폴링이 요청 제한 슬롯을 기다리는 시간도 60초 시간 상한에 넣는다. 상한 근처에서 2분 가까이 걸리던 검색이 받은 데까지 멈추고 `notes` 로 알린다 (#42)
- 편 번호·항공사 코드·공항 코드를 모를 때 없는 편명(`KE0`)이나 빈 문자열(`""`) 대신 `null` 이다 (`flight_number`, `airline_code`, 구간·여정 공항,
  스카이스캐너 `explore` 의 `code`) (#43)
- `search flights --brief` 가 편명을 모르는 구간의 `airline_code`, 운항 항공사 이름을 모르는 코드셰어 구간의 `operating_airline_code` 를 남긴다 (#44)
- 네이버 검색이 검색 결과가 아닌 응답(에러 본문 등)으로 끝나면 결과 0건 대신 `PARSE_ERROR` 다 (#45)
- `KeyError`·`IndexError` 로 난 `PARSE_ERROR` 의 `hint` 가 내부 오류일 수 있다며 `--verbose` 로 다시 실행하라고 안내한다 (#46)

## 0.2.0 — 2026-10-15

### 추가

- `search flights --brief` — 결과 모양은 그대로 두고 구간별 공항·시각·기종, 항공권 `url` 처럼 고르는 데 필요 없는 키를 뺀 요약 (두 소스) (#35)
- `flight --offer-urls N` — 판매처 예약 링크를 앞의 운임 N개에만 붙인다. 뒤 운임은 `url` 키가 없고, `0` 이면 링크 없이 가격만 (두 소스) (#36)

### 변경

- 파이프로 받는 `--help` 를 상자 그림 없는 평문으로 쓴다. 같은 도움말이 절반 아래로 짧고, 좁은 칸에서 설명이 `…` 로 잘리지 않는다 (터미널은 그대로) (#34)

### 수정

- 네이버 국제선 `aircraft` 가 기종을 모를 때 빈 문자열(`""`) 대신 `null` 이다 (#33)

## 0.1.0 — 2026-10-14

### 추가

- 첫 릴리스: 네이버 항공권·스카이스캐너에서 지금 살 수 있는 항공권·날짜별 최저가·목적지를 조회하는 CLI 와 `baenggipyo guide`
- `baenggipyo --version` — 설치한 버전과 커밋을 출력
- PyPI 에서 설치할 수 있다: `uv tool install baenggipyo`, `pipx install baenggipyo`, `pip install baenggipyo`, 설치 없이 `uvx baenggipyo`. `baenggipyo==0.1.0` 처럼 버전도 고를 수 있다 (#3)
