# AGENTS.md

네이버 항공권·스카이스캐너에서 **지금 살 수 있는 항공권**을 조회하는 CLI (`baenggipyo`). 구조와 규약은 [jibsa](https://github.com/Rukkakka/jibsa)를 따른다.
LLM 에이전트가 셸로 호출하는 도구라, 출력 형식과 에러 형식이 곧 API 다.
패키지 코드는 모두 `src/baenggipyo/` 아래에 있다.
개발 문서(구현 규약·소스별 개발 가이드)는 `references/`, 사용자 문서(CLI 사용법)는 `docs/`에 있다.
README 데모 GIF 는 `docs/demo/`, 그 녹화 도구(VHS 대본·후처리 스크립트)는 `scripts/demo/` 에 있다.

**지금 상태: 두 소스 모두 구현.** 네이버·스카이스캐너 모두 `search airports`·`search flights`·`flight`·`calendar`·`explore` 가 실제 API 로
동작한다. 스카이스캐너는 봇 차단이 있어 검증 요청도 아껴 쓴다 ([개발 가이드](references/providers/skyscanner.md#봇-차단)).

## 구조

| 경로 | 내용 |
| --- | --- |
| `cli/` | Typer 명령 (`limits`, `guide`, `naver`/`skyscanner` 하위 명령), JSON·에러 출력 (`output.py`), 공용 옵션 별칭 (`options.py`) |
| `core/http.py` | `BaseHTTPClient` — httpx 클라이언트 수명주기, 재시도 술어, `raise_for_status` |
| `core/ratelimit.py` | 프로세스 간 공유 요청 제한 (`RateLimiter`, `RateLimitedError`), 상태 디렉터리(`state_dir`)와 그 안의 JSON 쓰기(`write_json_atomic`) |
| `core/flight.py` | 항공권 검색 조건 베이스(`FlightConditions`), 공통 출력 모델(`Airport`, `SearchLeg`, `Segment`, `Journey`, `Itinerary`, `Offer`, `CalendarDay`, `Destination`)과 해당 없는 키·요약(`--brief`)에서 뺄 키 빼기(`FlightModel`), 판매처 운임 묶음 펴기(`shown_offers`), 날짜·시각 변환, 구간 공항 해석 묶기(`resolve_route`)·후보 고르기(`pick_place`), `notice` 문구 |
| `core/errors.py` | `BaenggipyoError` 계열 — CLI 가 `{"error": {...}}` 로 내보내는 에러. 다시 찾은 검색에 없는 항공권의 에러 고르기(`fare_missing_error`: 매진 또는 검색 미완료) |
| `core/schema.py` | `RequestModel` (`extra='forbid'`) / `ResponseModel` (`extra='allow'`), 응답 값으로 만드는 요청 모델 구간(`built_from_response`) |
| `core/korean.py` | 한글 이름 뒤 조사 고르기 (`particle`, `quoted`). [코드 형식 §1](references/code-style.md#1-언어) 규칙용이라 지금 호출부가 없어도 지우지 않는다 |
| `providers/<source>/` | `api/` 아래층 (`client.py` · `request/` · `response/`, 네이버는 SSE 를 읽는 `stream.py` 도) → `service/` 윗층. request·response·service 는 같은 이름의 기능별 모듈 ([모듈 구성](references/provider-conventions.md#모듈-구성)) |

소스별 비공식 API 의 엔드포인트·필드 의미는 각 provider 의 `api/client.py` docstring 과
`api/response/` 모델 설명에 적는다. 엔드포인트를 추가·수정하기 전에 먼저 읽고, 응답 형태는
CLI 직접 조회나 원본 응답으로 확인한다.

## 꼭 지킬 것

- **로그인 없는 기능만.** 계정·쿠키·API 키를 받지 않고, 로그인이 필요한 API(회원가·찜·가격 알림 등)나 사용자 대신 보내는 기능(예약·결제·문의)은 넣지 않는다. 승객 정보도 받지 않는다. 새 엔드포인트는 로그인 없이 호출되는지 먼저 확인한다. ([README 원칙](README.md#원칙-로그인-없는-기능만))
- **금액은 전부 원 정수.** 필드 이름에 단위를 붙인다 (`total_price_krw`). 총액과 1인 요금을 이름으로 구분하고, 한쪽만 오면 다른 쪽을 계산해 만들지 않는다. 다른 통화로 오면 환율을 추측하지 않는다.
- **시각은 각 공항 현지 시각** `YYYY-MM-DDTHH:MM`, 소요 시간은 분(`duration_minutes`). 시간대를 환산하지 않는다.
- **조회 결과는 stdout JSON, 에러는 stderr JSON.** 도움말·자동완성·`guide`·`--version` 은 CLI 텍스트이며 `--verbose` 진행 로그는 stderr 로 나간다. 새 에러는 `BaenggipyoError` 로 코드·힌트를 붙여 던진다.
- **값이 없을 때:** 해당 없음 → 키 삭제, 모름 → `null`, 비어 있음 → `[]`.
- **매진·만료 운임은 내보내지 않는다.** 목록에서는 거르고, 상세에서는 `FareUnavailableError`. 가격을 캐시하지 않는다.
- **서버가 거른 결과를 같은 조건으로 다시 거르지 않는다.** 소스의 분류(코드셰어 항공사 등)를 다른 기준으로 다시 판정하지 않는다. 직접 거르는 건 서버가 받지 않는 조건과 서버 필터 없는 대체 조회뿐이고, 확인할 수 없어 뺀 항공권은 사유를 `notes`로 알린다. ([서버 필터와 클라이언트 후처리](references/provider-conventions.md#서버-필터와-클라이언트-후처리))
- **원본 응답 통째·세션 토큰·쿠키·추적 ID 는 출력하지 않는다.** 예약은 `url` 의 원 사이트에서. 판매처 예약 링크는 원 사이트가 준 그대로 내며, 그 안의 검색 ID 는 예외다 ([내보내지 않는 정보](references/output-contract.md#내보내지-않는-정보)).
- **모든 HTTP 요청은 요청 제한 훅을 거친다.** 새 클라이언트도 `rate_limit_hooks()` 를 `event_hooks` 로 넘긴다. 폴링으로 다시 묻는 요청도 마찬가지다.
- **봇 차단을 우회하지 않는다.** 차단 페이지가 오면 에러로 알리고, 지문 위조 같은 우회 기법은 쓰지 않는다.
- **검증은 실제 API 로, 테스트는 최소로.** 코드를 바꾸면 CLI 직접 조회 같은 실제 API 호출로 확인한다 (요청은 최소로). 테스트는 아주 드문 엣지케이스나 틀어지면 안 되는 핵심 동작일 때만 추가한다. 전체 `pytest` 는 먼저 묻고, 평소에는 바꾼 부분 테스트 몇 개만 이름으로 골라 한 번 돌린다. ([검증과 테스트](references/development.md#검증과-테스트))

## 문서 지도

공통 개발 규약 (`references/`)

- [development.md](references/development.md) — 개발 환경 설치, 검증과 테스트 원칙
- [architecture.md](references/architecture.md) — 계층 구조, 요청 흐름, 요청 제한
- [provider-conventions.md](references/provider-conventions.md) — provider 모듈 작성 규칙 (action id 명명, 재시도, 폴링, 모델 배치, 서버 필터와 클라이언트 후처리)
- [output-contract.md](references/output-contract.md) — CLI 출력·에러·exit code 규약, 공통 항공권 필드 규칙
- [code-style.md](references/code-style.md) — 문자열, import 순서, pydantic 선언, 타입 힌트, docstring, 테스트 코드 형식
- [workflow.md](references/workflow.md) — 이슈 → 브랜치 → 커밋 → PR 절차
- [release.md](references/release.md) — 버전 번호 규칙, CHANGELOG 쓰는 법, 릴리스·태그·GitHub Release 절차

소스별 개발 가이드 (`references/providers/`) — 그 소스에만 있는 구현·예외

- [네이버](references/providers/naver.md) · [스카이스캐너](references/providers/skyscanner.md)

## 문서 갱신

README는 제작 목적·설치·LLM 연결 방법·문서 링크를 담는다. 사용자 문서는 `docs/`, 개발 문서는 `references/`에 둔다. CLI 문서는 LLM 에이전트에게 주는 사용 규칙이기도 하다.

`docs/llm.md` 는 에이전트가 가장 먼저 읽는 요약이고 `baenggipyo guide` 가 그대로 출력한다 (설치본에는 패키지 안으로 복사된다). 짧게 유지하고 (200줄 이하), 링크는 설치본에서도 열리게 GitHub 전체 주소로 쓴다. 옵션 표·실측 근거는 넣지 않고 명령 고르기·출력 읽기·틀리기 쉬운 점·에러 대응만 적은 뒤 자세한 절로 링크한다.

| 바뀐 것 | 고칠 문서 |
| --- | --- |
| 한 소스의 명령·옵션·반환 필드·조회 한도 | `docs/<source>.md` |
| 명령 추가·삭제, 에이전트가 틀리기 쉬운 해석 규칙(단위·누락값·ID·현지 시각 등)·에러 대응 | `docs/llm.md` |
| 공통 사용법(출력·에러 대응)·요청 제한 정책 | `docs/common.md` |
| 한 소스의 구현·예외·API 조사 결과 | `references/providers/<source>.md` |
| 여러 소스에 공통인 개발 규약 | `references/` 바로 아래 문서 |
| 사용자(에이전트 포함)에게 보이는 변경 | `CHANGELOG.md` 의 `Unreleased` ([쓰는 법](references/release.md#changelog-쓰는-법)) |

한 소스에만 해당하는 규칙·예외는 공통 문서에 적지 않고 그 소스 문서에 적는다. 명령의 인자·기본값·조회 한도·반환값·오류·주의사항은 해당 명령 절에서 설명한다. 요약 표는 해당 절과 일치시키고 다른 명령 절에 설명을 복사하지 않는다. 공통 규칙은 복사하지 않고 링크로 연결한다.

명령을 실제로 추가하면 해당 문서의 `예정` 표시를 지운다.

## 작업 절차

커밋 전에 **[references/workflow.md](references/workflow.md)** 를 읽는다. 요약:
**커밋 전에 `.github/ISSUE_TEMPLATE/` 템플릿으로 이슈를 연다.** `main` 에 직접 커밋하지 않고
`<type>/<slug>` 브랜치에서 `Refs #12` 로 이슈를 달아 커밋한다. 모든 변경은 PR 템플릿을 채운 PR 로
들어가고, 이슈는 PR 본문(`Closes #12`)에서 닫는다. 커밋 하나에 관심사 하나, `git add -A` 금지.
사용자에게 보이는 변경은 그 PR 에서 `CHANGELOG.md` 에 적는다. 버전 올리기·태그·GitHub Release 는
[references/release.md](references/release.md) 를 따른다.
