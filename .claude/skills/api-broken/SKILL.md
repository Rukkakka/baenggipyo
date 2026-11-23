---
name: api-broken
description: 매일 API 감시가 연 api-broken 이슈(또는 고장 난 명령)를 받아 깨진 명령을 재현하고, 원인과 고칠 계획을 보고한다. api-blocked 이슈는 고장이 아니라 확인하지 못한 상태인지만 본다. 감시 고장 대응을 요청받았을 때 쓴다.
argument-hint: "[이슈 번호, 비우면 열린 api-broken 이슈, 없으면 api-blocked 이슈]"
effort: high
---

`docs/development/README.md` 의 "매일 API 감시" 와 소스 가이드의 "API 변경 시 점검" 을 따른다. 감시 코드는 `scripts/monitor/api_check.py`, 워크플로는 `.github/workflows/api-monitor.yml` 이다. 이 명령은 원인을 찾고 계획을 보고하는 데까지다. 고치는 것은 허락받은 뒤 한다. 대상: `$ARGUMENTS`.

이슈 라벨은 둘이다.
- `api-broken`: 명령이 고장 났다 (응답 형식·엔드포인트 변경, 코드 버그).
- `api-blocked`: 같은 묶음을 사흘 연속 확인하지 못했다 (봇 차단·요청 제한). **고장이 아니다.**

1. **이슈 읽기.** `gh issue view <번호> --comments` (번호가 없으면 `gh issue list --label api-broken --state open` 으로 찾고, 없으면 `--label api-blocked` 로 찾는다). 가장 최근 본문·댓글의 표에서 고장 난(또는 확인 못 한) 소스·명령과 사유를 뽑는다.
2. **`api-blocked` 이면 여기서 따로 본다.** 감시 러너에서만 막히는지 로컬에서 그 명령을 표에 나온 그대로 한 번 불러 본다 (스카이스캐너는 `.venv/bin/baenggipyo limits` 로 남은 요청 수를 확인한 뒤).
   - 로컬이 정상이면 코드 문제가 아니라 감시 러너가 막힌 것이라고 보고하고 멈춘다. 그 묶음을 덜 자주 돌릴지는 README "매일 API 감시" 대로 사용자와 정한다.
   - 로컬도 막히면 차단·요청 제한 상태만 알리고, 같은 시험을 거듭하지 않는다.
3. **재현** (`api-broken`). 고장 난 명령을 표에 나온 그대로 `.venv/bin/baenggipyo ...` 로 부른다. 요청은 최소로 한다 (스카이스캐너는 `limits` 의 남은 수 안에서). 결과를 판정 사유와 견준다.
   - 지금은 정상이면 그날만 난 장애일 수 있다. 재현 결과와 함께 보고하고 멈춘다.
   - `RATE_LIMITED`·`NETWORK_ERROR` 면 고장이 아니라 확인 못 함이다. 요청 제한이 풀린 뒤 다시 본다.
4. **원인 찾기.** 그 소스 가이드(`docs/development/providers/<소스>.md`) "API 변경 시 점검" 의 순서대로 보고, 코드는 client → request → response → service → CLI 순으로 본다. 받은 원본 응답을 저장해 응답 형식이 바뀌었는지, 엔드포인트·헤더가 바뀌었는지, 코드 버그인지 가른다.
   - 고장 난 소스가 하나면 직접 찾는다. 웹이 지금 쓰는 API·헤더·필드를 캡처해야 하면 출력이 커서 `web-parity-investigator` 하나에 맡긴다 (Opus medium, 질문: "이 명령이 왜 깨졌나. 웹이 지금 보내는 요청·응답과 baenggipyo 의 것이 어디서 다른가").
   - 두 소스가 모두 고장이면 소스마다 조사원 하나를 한 번에 띄운다 (같은 소스에 조사원이 더 필요하면 차례로. `docs/development/agents.md` "동시에 띄울 때").
5. **보고와 계획.** AGENTS.md 작업 절차대로 쉬운 말로 설명하고 허락을 받는다.
   - 원인과 근거 (재현 명령, 응답의 핵심 필드, 웹 값)
   - 고칠 파일과 내용, 직접 할지 나눌지
   - 확인 방법: 고친 명령의 실제 조회, 필요하면 그 소스 감시를 로컬로 한 번 (`.venv/bin/python scripts/monitor/api_check.py run <묶음> --out <scratchpad>/result.json`). 스카이스캐너 묶음은 봇 차단을 아끼려고 고친 명령만 조회하고, 전체 감시는 왜 필요한지 계획에 적어 허락받을 때만 돌린다.
   - 고장을 고친 것이면 머지 뒤 바로 패치 릴리스가 필요하다는 것 (`docs/development/release.md` "릴리스 절차": 조회가 깨진 것을 고친 수정). 릴리스는 사용자가 `/release` 로 시작한다.
