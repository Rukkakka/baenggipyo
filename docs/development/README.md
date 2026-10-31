# 공통 개발 환경

[개발 규약](../../AGENTS.md) · [코드 형식](code-style.md) · [작업 절차](workflow.md)

## 설치

코드를 고치려면 저장소를 받아 편집 가능 모드로 설치한다. 고친 코드가 바로 반영된다. Python·git 설치는 [설치 문서의 준비물](../install.md#1-준비물)을 따른다.

```bash
# macOS·Linux
git clone https://github.com/Rukkakka/baenggipyo && cd baenggipyo

python3.11 -m venv .venv          # 또는 uv venv --python 3.11
.venv/bin/pip install -e ".[dev]" # 또는 uv pip install -e ".[dev]"

.venv/bin/baenggipyo --help            # source .venv/bin/activate 후에는 baenggipyo 만 쳐도 된다
```

```powershell
# Windows (PowerShell) — bin 대신 Scripts 폴더를 쓴다
git clone https://github.com/Rukkakka/baenggipyo; cd baenggipyo

py -m venv .venv                       # 또는 uv venv --python 3.11
.venv\Scripts\pip install -e ".[dev]"  # 또는 uv pip install -e ".[dev]"

.venv\Scripts\baenggipyo --help             # .venv\Scripts\Activate.ps1 후에는 baenggipyo 만 쳐도 된다
```

Windows 는 [README 의 Windows 에서 쓸 때](../../README.md#windows-에서-쓸-때)(UTF-8 출력 설정)도 그대로 적용된다. `Activate.ps1` 이 실행 정책 때문에 막히면 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 를 한 번 실행한다.

## 코드 검사

[코드 형식](code-style.md) 규칙 중 도구로 잡을 수 있는 것은 ruff 와 mypy 가 검사한다. 설정은 `pyproject.toml` 에 있다. 코드를 고쳤으면 둘 다 0건이어야 한다.

```bash
.venv/bin/ruff check          # 따옴표, import 순서, 안 쓰는 import, 타입 힌트 누락
.venv/bin/ruff check --fix    # import 정렬·따옴표처럼 자동으로 고칠 수 있는 것은 고친다
.venv/bin/mypy                # src 전체 타입 검사
```

Windows 는 `.venv\Scripts\ruff`, `.venv\Scripts\mypy` 로 부른다.

- `ruff format` 은 쓰지 않는다. 여러 줄로 쓰는 필드 선언([코드 형식 §4](code-style.md#4-pydantic-선언))을 한 줄로 합친다.
- import 바로 위에 주석을 달면 ruff 가 그 앞에 빈 줄을 넣어 같은 그룹이 갈라진 것처럼 보인다. import 에 다는 주석은 줄 끝에 쓴다.
- [코드 형식 §4](code-style.md#4-pydantic-선언) 의 별칭·기본값 위치는 ruff·mypy 가 잡지 못해 `tests/test_schema.py` 가 검사한다.
- mypy 오류를 `# type: ignore` 로 덮지 않는다. 앞에서 이미 확인해 None 일 수 없는 값이면 `assert` 로 좁히고 왜 그런지 주석을 단다. 정말 필요할 때만 오류 코드를 붙여 쓴다 (`# type: ignore[assignment]`).

## 검증과 테스트

### 검증은 실제 API 로

- 코드를 바꾸면 CLI 직접 조회나 원본 응답 확인처럼 실제 API 호출로 동작을 확인하는 것이 기본이다. 가짜 응답(픽스처·`MockTransport`)으로만 확인했다고 하지 않는다. 실제 API 를 부르는 테스트(live 테스트)는 두지 않고 CLI 직접 조회로 확인한다. 매일 새벽 도는 [API 감시](#매일-api-감시)만 예외다 (pytest 가 아니라 따로 도는 워크플로다).
- 실제 API 로 재현할 수 없는 상태(커서가 멈춘 응답 등)만 가짜 응답으로 확인하고 그 이유를 적는다.
- 요청 수는 최소로 하고 요청 제한 훅을 거친다 (두 소스 모두 봇 차단이 있을 수 있다. 조사 결과는 소스 개발 가이드에 적는다).
- 문서·문구만 바꿨으면 링크·코드 선언·출력 필드 대응만 검토하고 pytest·실제 호출은 하지 않는다.

### 테스트 추가는 두 경우만

1. 실제 API 로 재현하기 어려운 아주 드문 엣지케이스
2. 틀어지면 안 되는 핵심 동작 (출력 계약·만료 운임 제외·서버 필터 원칙 같은 공통 규칙)

기존 테스트를 넓히는 것도 같은 기준이다. 추가할 때는 둘 중 무엇인지 PR·보고에 적는다. 그 밖의 경우(일반 분기, 요청 파라미터 모양, 문구, 구현을 그대로 따라 쓰는 검증, 다른 테스트와 겹치는 검증)에는 추가하지 않는다.

### pytest 실행 범위

```bash
.venv/bin/pytest tests/test_cli.py::test_name   # 평소: 바꾼 부분 테스트 몇 개만 이름으로
.venv/bin/pytest                                  # 전체 (네트워크 없음) — 실행 전에 사용자에게 묻는다
```

Windows 는 `.venv/bin/pytest` 대신 `.venv\Scripts\pytest` 로 부른다.

- **전체 `pytest` 는 실행 전에 사용자에게 묻는다.** PR 에서는 [CI](../../.github/workflows/ci.yml) 가 전체를 돌린다.
- 평소에는 바꾼 함수를 직접 검사하는 테스트 몇 개만 `tests/test_x.py::test_name` 으로 골라 한 번 실행한다. 파일 전체, 넓은 `-k` 키워드, 바꾼 함수를 부르는 테스트를 모두 모아 돌리는 것도 하지 않는다. 실패하면 그 테스트만 다시 돌린다.
- 문구·문서·이름 변경처럼 깨질 위험이 없으면 바꾼 부분의 테스트도 돌리지 않는다.

### 픽스처와 테스트 환경

- 픽스처는 `tests/fixtures/<source>/<action_id>.json`. 실제 응답 구조를 본뜬 **가상 데이터**다. 실제 응답에 섞인 세션 토큰·쿠키·추적 ID 는 커밋하지 않는다. 만드는 법은 [새 엔드포인트 추가 순서](provider-conventions.md#새-엔드포인트-추가-순서)에 있다.
- 픽스처는 테스트가 읽는 것만 둔다. 응답 형태를 보여 주는 참고 자료로 두지 않고, 테스트를 지워 아무도 읽지 않게 된 픽스처는 함께 지운다.
- `conftest.py` 가 `BAENGGIPYO_STATE_DIR` 을 임시 디렉터리로 돌려 `~/.cache/baenggipyo` 를 건드리지 않는다.
- 테스트 코드 형식(파일·함수 이름, `MockTransport` 끼우는 법)은 [코드 형식](code-style.md#8-테스트-코드)에 있다.

## 매일 API 감시

비공식 API 는 사이트가 응답을 바꾸면 조용히 깨진다. 사용자보다 먼저 알려고 [API 감시 워크플로](../../.github/workflows/api-monitor.yml)가
한국 시간 매일 04:17 에 두 소스의 모든 CLI 명령을 실제로 부른다 ([scripts/monitor/api_check.py](../../scripts/monitor/api_check.py)).
CI 는 여전히 실제 API 를 부르지 않는다.

- **무엇을 부르나:** 두 소스의 `search airports`·`search flights`·`flight`·`calendar`·`explore` 를 국제선·국내선으로 (편도, 30일 뒤,
  결과가 0건일 리 없는 인기 노선). 네이버 국내선은 왕복도 부른다. 결과 모양(`legs[]`)과 요청(오는 편 검색, 두 편 예약 가격)이 편도와
  다르다. 네이버는 출발 시각대(`--depart-time`·`--return-time`)를 건 국제선 편도·국내선 왕복 검색도 부른다. 서버 필터라 필터 키가 바뀌어
  무시되면 결과가 그대로 나와 다른 검색으로는 모른다 (스카이스캐너는 baenggipyo 가 걸러 부르지 않는다). `flight` 는 바로 앞 검색의 첫 항공권
  ID(국내선 왕복은 가는 편·오는 편 첫 ID 둘)로 부른다. 날짜는 한국 시간으로 센다. 묶음은 네이버, 스카이스캐너
  국제선, 스카이스캐너 국내선 셋이고 러너를 나눈다. 스카이스캐너 명령을 모두 부르면 40번 남짓이라 한 러너로는 시간당 상한(30회)에 막혀 한 시간
  넘게 기다린다. 막힌 묶음을 다른 러너로 다시 돌리지는 않는다 (막힌 날은 확인 못 함으로 남긴다).
- **스카이스캐너는 접속 버릇만 크롬처럼:** 러너는 데이터센터 IP 라 스카이스캐너 봇 차단에 첫 요청부터 막히곤 한다. 감시는 검증용이라
  스카이스캐너 묶음만 [disguise.py](../../scripts/monitor/disguise.py)(`api_check.py run --disguise`)로 부른다. `SkyscannerClient` 의 httpx
  전송만 curl_cffi(`impersonate='chrome'`, 워크플로에서 `curl_cffi==0.16.3` 설치)로 바꾸고, 요청 제한·로그 훅, 헤더, 응답 해석은 CLI 와 같다.
  설치되는 CLI 에는 넣지 않고 캡차는 풀지 않는다 (막히면 확인 못 함). [AGENTS.md](../../AGENTS.md) 의 "봇 차단을 우회하지 않는다" 는 CLI
  원칙이라 감시는 예외다.
- **판정:** 명령마다 셋으로 나눈다.

  | 판정 | 기준 |
  | --- | --- |
  | 정상 | exit 0, 결과 1건 이상 |
  | 고장 | `PARSE_ERROR`·`INTERNAL_ERROR`·`NOT_FOUND`·4xx·500 `UPSTREAM_ERROR` 같은 에러, 결과 0건, JSON 이 아닌 출력, 결과 파일 없음(조회 잡 실패), 출발 시각대를 건 검색에 그 시각대 밖 항공권(끝 시 정각 출발은 뺀다) |
  | 확인 못 함 | `RATE_LIMITED`(봇 차단·요청 제한), `NETWORK_ERROR`, 502·503 같은 서버 일시 오류이거나 `SEARCH_INCOMPLETE` 인 `UPSTREAM_ERROR`, `FARE_UNAVAILABLE`(검색 직후 매진), 5분 안에 끝나지 않음, 앞 검색이 결과를 주지 않아 건너뛴 상세, 판매처를 모두 뺀 까닭을 `notes` 로 알린 상세(`offers` 0건) |

  500 은 고장으로 본다. 두 소스 모두 요청 형식이 맞지 않으면 늘 같은 500 을 줘서(네이버는 헤더가 빠지면, 스카이스캐너 달력은 도시를 `SEL` 로
  주면) 사이트가 바뀐 것일 수 있다. 사이트가 잠깐 아파 500 을 준 날은 고장으로 잘못 알릴 수 있다.

- **알림:** 고장이면 `api-broken` 이슈를 열고(열려 있으면 댓글) 실행이 실패로 끝나 GitHub 이 메일로 알린다. 모든 묶음이 정상이면 그
  이슈를 닫는다. 같은 묶음이 사흘 연속 확인 못 함이면 `api-blocked` 이슈를 열고(열려 있으면 새로 연속이 된 묶음을 댓글로), 연속인 묶음이
  없어지면 닫는다. 연속은 실행 횟수가 아니라 한국 날짜로 센다 (하루 여러 번 돌린 날은 그날 마지막 실행, 실행이 없는 날이 끼면 끊긴다).
  이슈는 `main` 의 예약·수동 실행만 고친다. 막힘이 이어지면 그 묶음을 덜 자주 돌리는 것을 검토한다.
- **명령을 추가·삭제하면** `api_check.py` 의 `GROUPS` 도 고친다. 국제선·국내선이 다르게 동작하면 둘 다 넣는다.
  `tests/test_cli.py::test_every_source_command_is_in_api_monitor` 가 CLI 명령 목록과 대조해 빠뜨리면 실패한다.
- **수동 실행:** Actions 의 "API 감시" → Run workflow, 또는 `gh workflow run api-monitor.yml`. 로컬에서는
  `python scripts/monitor/api_check.py run naver --out result.json` 처럼 묶음 하나를 돌린다 (스카이스캐너 묶음은 봇 차단을 아끼려고 로컬에서
  돌리지 않는다. 돌린다면 `pip install curl_cffi==0.16.3` 뒤 `--disguise`). 이슈 처리는 `report result.json --issues --dry-run` 으로 보낼 `gh` 명령만 본다.
- 감시 파일(`scripts/monitor/`, 워크플로)을 고친 PR 에서도 한 번 돌고, 이슈는 건드리지 않는다 (다른 브랜치의 수동 실행도). 고장이면 그 PR 의
  검사가 실패한다. 실행을 취소하면 결과 정리 잡은 돌지 않는다.
- 공개 저장소에 60일 동안 활동이 없으면 GitHub 이 예약 실행을 끈다. 꺼졌으면 Actions 의 "API 감시" 에서 다시 켠다.
