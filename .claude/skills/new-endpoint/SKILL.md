---
name: new-endpoint
description: 한 소스에 새 엔드포인트(새 명령, 또는 기존 명령의 새 조회 경로)를 더할 때 먼저 확인할 것을 보고 계획을 세운 뒤, 허락받으면 정해진 순서대로 구현한다.
argument-hint: "<소스> <무엇을 하는 엔드포인트인지>"
effort: high
---

`docs/development/provider-conventions.md` 의 "새 엔드포인트 추가 순서" 와 AGENTS.md "꼭 지킬 것" 을 따른다. 대상: `$ARGUMENTS`. 한 소스 안에서 순서대로 하는 일이라 기본은 직접 한다.

1. **먼저 확인한다** (파일을 고치지 않는 조사).
   - 로그인 없이 호출되는가 (계정·쿠키·API 키 없이). 아니면 넣지 않는다고 보고하고 멈춘다.
   - 웹 화면이 이 기능에 실제로 하는 요청(API·변수·기본값·정렬·계산). `docs/development/provider-conventions.md` "웹 화면과의 대응" 의 "안 되는 것" 에 해당하면 넣지 않고, "되는 것" 에 해당하면 그 소스 가이드 "웹과 다른 점" 절(없으면 만든다)에 적는다. 웹 캡처가 필요하면 출력이 커서 `web-parity-investigator` 하나에 맡긴다 (`model: opus`, effort high, 범위가 좁으면 medium). 필요하면 `/web-parity` 로 조사한다.
   - 새 엔드포인트의 응답 형태는 CLI 직접 조회나 받은 원본 응답으로 본다. 추측하지 않는다 (스카이스캐너는 `baenggipyo limits` 로 남은 요청 수를 보고 최소로 부른다).
   - 그 소스의 `api/client.py` docstring, `api/response/` 모델, `docs/development/providers/<소스>.md` 를 읽어 이미 있는 기능·모듈과 겹치지 않는지, 어느 기능 모듈에 들어갈지 본다.
   - 출력에 원본 응답 통째·세션 토큰·쿠키·추적 ID 가 섞이지 않는지, 금액을 원 정수로 낼 곳(`*_krw`, 총액과 1인 요금 구분), 시각을 현지 시각으로 낼 곳, 매진·만료 운임을 거를 곳.
2. **계획을 설명하고 허락받는다** (AGENTS.md 작업 절차). 고칠 파일을 순서대로 적는다:
   `api/client.py`(ClassVar + sync/async 메서드, docstring 에 엔드포인트·필드 의미) → `api/request/<기능>.py` → `api/response/<기능>.py` → `service/<기능>.py` → `cli/<소스>.py` → 문서.
   함께 고칠 곳 (AGENTS.md "문서 갱신" 표):
   - `docs/<소스>.md` 의 그 명령 절
   - 명령을 더했으면 `docs/llm.md` 와 `scripts/monitor/api_check.py` 의 `GROUPS` (국제선·국내선이 다르게 동작하면 둘 다)
   - `CHANGELOG.md` 의 `Unreleased`
   - 소스 개발 가이드의 Action id 목록, 웹과 다르게 한 곳은 "웹과 다른 점" 절
3. **구현한다.** 위 순서대로. 헤더는 웹이 그 API 에 실제로 붙이는 것만 실측해 넣고, 요청은 요청 제한 훅(`rate_limit_hooks()`)을 거친다. 테스트는 `docs/development/README.md` "검증과 테스트" 기준에 맞을 때만 더하고, 픽스처는 실제 응답 구조에 값은 모두 가상으로 바꾼다.
4. **확인한다.** `.venv/bin/ruff check`·`.venv/bin/mypy` 0건, 바꾼 테스트만 이름으로, 새 동작을 CLI 로 실제 조회 1회. 명령을 더했으면 `tests/test_cli.py::test_every_source_command_is_in_api_monitor`. 결과를 보고하고 멈춘다 (커밋은 시킬 때만).
