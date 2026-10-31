# Changelog

baenggipyo 의 버전별 변경 기록. 사용자(에이전트 포함)에게 보이는 변경만 적고, 아직 릴리스하지 않은 변경은
`Unreleased` 에 쌓는다. 번호 규칙과 쓰는 법은 [docs/development/release.md](docs/development/release.md) 에 있다.

## Unreleased

## 0.5.1 — 2026-10-30

### 수정

- 네이버 국제선 `flight` 가 판매처 0곳·가격 `null` 로 나오던 문제. 네이버가 판매처 조회에 예약 링크 서명을 요구하게 바뀌었다 (2026-10-29 부터).
  이제 서명을 함께 보내고, 판매처가 하나도 오지 않으면 빈 `offers` 대신 `UPSTREAM_ERROR` 를 낸다 (#115)

## 0.5.0 — 2026-10-24

### 추가

- `baenggipyo guide naver`·`baenggipyo guide skyscanner`: 그 소스만의 주의점을 출력한다 (#101)

### 변경

- `baenggipyo guide` 가 공통 부분만 출력한다. 소스별 주의점은 `guide <소스>` 로 옮겼다. 처음 읽는 가이드가 짧아졌다 (#101)

### 수정

- 네이버에서 출발·도착을 `인천`·`김포` 처럼 공항이 하나뿐인 도시 이름으로 주면 도시로 찾아 다른 공항 출발편(인천이면 김포, 김포면 인천)까지 섞이던 문제. 이제 그 공항으로 찾는다 (#102)
- 요청 제한을 끄고 라이브러리로 쓸 때 서버 오류 메시지에 스카이스캐너 봇 차단 페이지의 추적 ID(`uuid`, `vid`)와 캡차 주소가 그대로 실리던 문제 (#103)

## 0.4.0 — 2026-10-21

### ⚠️ 호환 깨짐

- `search flights` 가 기본으로 요약(지금까지의 `--brief`)을 낸다. 구간별 공항·시각·소요 시간·기종, 항공권 `url`·`source`·`trip_type`·`cabin` 이
  필요하면 `--full` 을 붙인다 (`--brief` 도 그대로 받는다) (#84)
- `flight` 가 기본으로 맨 앞(최저가) 판매처 운임에만 예약 링크(`offers[].url`)를 붙이고, 링크를 뺀 운임이 있으면 `notes` 로 알린다. 링크가
  모두 필요하면 `--offer-urls` 에 `--limit` 과 같은 수를 준다 (#84)
- 결과 맨 앞의 `notice`(늘 같은 안내)를 뺐다. 같은 내용은 `baenggipyo --help` 와 `baenggipyo guide` 에 있다. `notice` 를 읽던 쪽은 지운다 (#79)
- 판매처 수 `offer_count` 를 `seller_count` 로 바꿨다 (`search flights`·`flight`, 네이버 국내선 `flight` 포함). 읽는 쪽도 새 이름으로 고친다 (#80)
- 네이버 국내선 편도 `search flights` 가 국제선과 같은 `returned`·`itineraries` 로 낸다. 전에는 `legs[0].itineraries` 였다. `legs` 는 국내선
  왕복에만 온다 (#81)
- `calendar` 의 `days[].direct` 를 뺐다. 두 소스 모두 날마다 직항인지 주지 않아, 입력한 `--direct` 를 되풀이하거나 늘 `null` 이었다. 네이버는
  `--direct` 로 찾았는지를 결과 맨 위 `direct` 로 낸다 (#82)
- 스카이스캐너 `explore` 의 `destinations[].code` 가 `null` 이고, 스카이스캐너 도시 코드(`CJUA`)는 `sky_code` 로 옮겼다. IATA 가 아니라 검색에
  쓸 수 없으니 `city` 이름으로 찾는다. 스카이스캐너 `search airports` 도 IATA 코드를 주지 않은 도시는 `code` 가 `null` 이다 (도시 코드를 내지 않는다) (#77)
- 대문자 4글자 코드(`CJUA`, ICAO `RKSI`)를 출발·도착지로 주면 비슷한 후보를 고르거나 권하지 않고 `AIRPORT_NOT_FOUND` 다. 스카이스캐너 `CJUA`(제주)를
  넣으면 `AIRPORT_AMBIGUOUS` 로 청주(`CJJ`)를 권하던 문제다. 요청 전에 걸러 요청이 들지 않는다. 대문자로만 쓴 이름(`JEJU`)도 여기에 걸리니
  한글 도시 이름(`제주`)이나 IATA 코드로 다시 넣는다 (#76)
- 스카이스캐너 `flight` 의 `offers[].fare_name` 이 판매처 여러 곳에서 따로 예약하는 조합의 줄에만 있다. 전에는 다른 줄마다 `null` 이었다 (#89)

### 추가

- `search flights` 의 `--depart-time`·`--return-time` — 가는 편(다구간은 첫 구간)·오는 편 출발 시각대 `시작시-끝시` (`06-12`, `18-24`). 경계는
  0·6·9·12·15·18·21·24시다. 네이버는 서버가 거르고(국제선·국내선), 스카이스캐너는 받은 결과에서 baenggipyo 가 거른다 (#91)
- 환경 변수 `BAENGGIPYO_PLAIN=1` — 터미널에서 불러도 JSON 을 한 줄로, `--help` 와 에러를 꾸밈 없이 쓴다. 명령을 터미널 화면(PTY)으로 돌리는
  에이전트(VS Code Copilot, Gemini CLI 대화형 셸 등)에서 도움말이 3분의 1, JSON 이 60% 남짓으로 준다 (#90)
- `--cabin`·`--sort` 가 영어 값도 받는다 (`economy`·`premium_economy`·`business`·`first`, `price`·`departure`·`duration`). 대소문자·띄어쓰기·
  끝의 `class` 는 가리지 않는다 (`프리미엄 일반석`, `Business Class`). 결과 값은 한국어 그대로다 (#83)
- `calendar` 결과 맨 위 `cheapest_days` — 싼 순 3일 (같은 값이면 이른 날 먼저). `days` 는 날짜 순 그대로다 (#82)
- `limits` 의 `requests_remaining_hour`(이번 1시간에 남은 요청)와 `hour_slot_wait_seconds`(다 썼을 때 한 칸이 비기까지 남은 초). 시간당 상한이
  있는 스카이스캐너만 (#85)
- 스카이스캐너 명령이 이번 1시간에 남은 요청이 10회 밑이면 결과 `notes` 로 알린다 (#85)

### 변경

- `explore` 가 결과마다 붙이던 같은 안내를 가격이 추정가라는 하나로 줄였다. 스카이스캐너는 그 하나에 가격이 그 기간 어느 날의 최저가라 다른 날은
  몇 배 비쌀 수 있다는 것과 `calendar` 로 그 날을 먼저 찾으라는 것을 담는다 (`--round-trip` 은 `calendar` 가 편도만 되어 그 달 날짜로
  `search flights` 하라고). `sky_code` 안내(네이버는 나라 이름 안내)는 뺐다 (#79)
- 네이버 `flight` 가 운임이 `--limit` 보다 많을 때 `notes` 에 판매처 수와 운임 수를 함께 적는다 (`판매처 21곳의 운임 114개 중 앞의 10개`) (#80)
- 결과마다 같은 안내를 뺐다: 스카이스캐너 `search flights` 의 정렬 안내(`가격 순으로 baenggipyo 가 세웠습니다`)와 `flight` 의 `가격은 방금 다시
  검색한 값입니다`. 같은 내용은 사용 문서와 `--help` 에 있다 (#79)
- 네이버 국내선 왕복 `search flights` 의 `가는 편이 더 있을 수 있습니다`·`오는 편이 …` 두 줄을 한 줄(`가는 편·오는 편이 …`)로 합쳤다 (#88)
- 네이버 국내선 `flight` 의 `FARE_UNAVAILABLE` hint 가 같은 편의 다른 좌석 등급이 남아 있을 수 있다고 알린다. 좌석 등급 하나만 팔려도 편을
  통째로 후보에서 빼던 안내였다 (#88)
- LLM 가이드(`baenggipyo guide`): 공항은 코드나 이름으로 바로 검색하고 `search airports` 를 먼저 부르지 않게(입력마다 요청 1회가 더 든다),
  네이버 국제선은 상세 맨 위 `url` 이 그 항공권을 고른 화면이라 링크만 필요하면 `--offer-urls 0` 으로 충분하다고 안내한다 (#92)

### 수정

- 네이버 국제선 `search flights --sort 가격`(기본)에서 카드 할인이 없는 싼 항공권이 빠지던 문제. 네이버 가격순은 카드 할인을 포함한
  가격 기준이라 대표 가격 2~4위가 11번째 밖으로 밀리기도 했다. 이제 `--limit` 의 3배를 받아 대표 가격(`price_per_adult_krw`) 순으로 세운 뒤
  자른다. 순서가 다르다는 `notes` 는 없어졌다 (#87)
- 스카이스캐너 `explore` 의 `country`·`country_code` 가 늘 `null` 이던 문제. 나라가 응답의 다른 자리로 옮겨 갔다 (#78)
- `baenggipyo naver --help`·`naver search --help` 명령 목록에서 `flight`·`flights` 설명이 괄호 중간에 잘리던 문제 (#75)

## 0.3.1 — 2026-10-20

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
