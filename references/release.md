# Release

버전 번호를 정하는 규칙과 릴리스 절차. 변경이 `main` 에 들어가기까지의 절차는 [workflow.md](workflow.md) 를 본다.

## 버전은 `pyproject.toml` 한 곳에

버전은 `pyproject.toml` 의 `version` 에만 적는다. `baenggipyo --version` 은 설치 메타데이터에서 읽으므로
(`cli/app.py` 의 `version_text()`) 코드나 문서에 버전 문자열을 복사하지 않는다.

사용자는 `git+https://github.com/Rukkakka/baenggipyo` 로 `main` 의 최신 커밋을 설치한다. 그래서 릴리스 사이의 커밋은
버전이 같고, `baenggipyo --version` 이 설치한 커밋을 함께 보여 준다.

| 설치 방법 | `baenggipyo --version` |
| --- | --- |
| git 주소 (`uv tool install git+...`, `pipx`, `pip`) | `baenggipyo 0.2.0 (커밋 a1b2c3d)` |
| 편집 가능 설치 (`pip install -e .`) | `baenggipyo 0.2.0 (개발 설치)` |
| 로컬 경로 (`uv tool install .`) | `baenggipyo 0.2.0` |

## 번호 붙이는 법

[SemVer](https://semver.org/lang/ko/) 를 따르고 `0.작은.패치` 로 시작한다. baenggipyo 는 출력 JSON 이 곧 API 라서,
[출력 규약](output-contract.md)과 에이전트가 읽는 출력이 어떻게 바뀌었는지로 올릴 자리를 정한다.

| 바뀐 것 | 올리는 자리 | 예 |
| --- | --- | --- |
| 버그 수정, 사이트 API 변경 대응 (출력 모양은 그대로), 문서 오류 수정 | 패치 | 0.1.0 → 0.1.1 |
| 명령·옵션·출력 필드·에러 코드 추가, 기본값·조회 한도 변경 | 작은 | 0.1.1 → 0.2.0 |
| 명령·옵션·필드 이름 변경·삭제, 값의 뜻·단위 변경, 에러 코드·exit code 변경 (호환 깨짐) | 작은 + `⚠️ 호환 깨짐` 표시 | 0.2.0 → 0.3.0 |

- 한 릴리스에 여러 종류가 섞이면 가장 큰 것 하나를 기준으로 한다. 작은 자리를 올리면 패치는 0 으로 돌아간다.
- 출력 규약이 안정되면 1.0.0 을 낸다. 그 뒤로는 호환 깨짐이 앞자리를 올린다 (1.4.2 → 2.0.0).
- 의존성·도구·테스트·내부 리팩터링만 바꾼 변경은 버전을 올릴 이유가 아니다. 다른 변경과 함께 나간다.

## CHANGELOG 쓰는 법

사용자(에이전트 포함)에게 보이는 변경은 **그 PR 에서** [CHANGELOG.md](../CHANGELOG.md) 의 `## Unreleased` 아래에
적는다. 릴리스할 때 몰아서 쓰면 왜 바꿨는지 잊는다.

- 분류는 `⚠️ 호환 깨짐` · `추가` · `변경` · `수정` 네 가지를 이 순서로 둔다. 항목이 없는 분류 제목은 쓰지 않는다.
  - `⚠️ 호환 깨짐` — 위 표의 호환 깨짐. 삭제도 여기에 적는다.
  - `추가` — 새 명령·옵션·출력 필드·에러 코드.
  - `변경` — 호환은 지키면서 동작이 달라진 것 (기본값·조회 한도·정렬 등).
  - `수정` — 버그, 사이트 API 변경 대응, 문서 오류.
- 한 줄에 하나, 사용자 입장에서 무엇이 달라졌는지와 이슈 번호를 적는다 (이슈는 PR 보다 먼저 열어 번호를 알 수 있다).
  내부 리팩터링·테스트·도구 변경은 적지 않는다.
- 호환 깨짐은 쓰는 쪽이 무엇을 고쳐야 하는지까지 적는다.

```markdown
## Unreleased

### ⚠️ 호환 깨짐

- 출력 필드 `duration` 을 `duration_minutes` 로 바꿈. 읽는 쪽도 새 이름으로 고친다 (#28)

### 추가

- 스카이스캐너 `search flights` 의 `--max-stops` 옵션 (#25)

### 수정

- 네이버 `calendar` 가 응답 구조 변경으로 `PARSE_ERROR` 를 내던 문제 (#31)
```

## 릴리스 절차

변경이 어느 정도 모이면 릴리스한다. 정해진 주기는 없다. 사용자는 `upgrade` 할 때 `main` 을 바로 받으므로,
릴리스는 급한 수정을 내보내는 일이 아니라 "이 커밋이 몇 버전"인지 이름을 붙이는 일이다.

1. 🧹 잡무 템플릿으로 이슈를 열고 `chore/release-0.2.0` 브랜치를 딴다 ([workflow.md](workflow.md)).
2. 한 커밋으로 아래 두 가지를 바꾼다.
   - `pyproject.toml` 의 `version` 을 올린다. `Unreleased` 에 `수정` 만 있으면 패치, 다른 분류가 하나라도 있으면
     작은 자리 (1.0 이후 `⚠️ 호환 깨짐` 은 앞자리).
   - `## Unreleased` 를 `## 0.2.0 — 2026-11-02` 로 바꾸고, 그 위에 빈 `## Unreleased` 를 새로 둔다.
3. PR 을 열고 머지한다.
4. 머지된 `main` 커밋에 태그를 달고 push 한다.

   ```
   git switch main && git pull
   git tag -a v0.2.0 -m 'v0.2.0'
   git push origin v0.2.0
   ```

5. 그 태그로 GitHub Release 를 만든다. 본문은 CHANGELOG 의 해당 절이고, 저장소 첫 화면의 Releases 에 버전과
   변경 내용이 보인다.

   ```
   awk '/^## 0\.2\.0/{f=1; next} /^## /{if (f) exit} f' CHANGELOG.md > /tmp/notes.md
   gh release create v0.2.0 --verify-tag --title v0.2.0 --notes-file /tmp/notes.md
   ```

   `--verify-tag` 는 push 한 태그가 없으면 멈춘다. 빼면 `gh` 가 기본 브랜치 끝에 태그를 새로 만든다.
6. GitHub 주소로 새로 설치해 버전을 확인한다. 쓰던 설치본을 건드리지 않게 임시 가상환경에 설치하고, 실제 조회도
   한 번 해 본다.

   ```
   python3 -m venv /tmp/baenggipyo-check
   /tmp/baenggipyo-check/bin/pip install -q git+https://github.com/Rukkakka/baenggipyo
   /tmp/baenggipyo-check/bin/baenggipyo --version   # baenggipyo 0.2.0 (커밋 <머지 커밋>)
   /tmp/baenggipyo-check/bin/baenggipyo naver search airports 오사카
   rm -rf /tmp/baenggipyo-check
   ```

태그는 머지 뒤 `main` 에만 단다. 브랜치 커밋에 달면 squash·rebase 머지 때 `main` 에 없는 커밋을 가리킨다.
