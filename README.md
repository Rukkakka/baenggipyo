<p align="center">
  <img src="https://raw.githubusercontent.com/Rukkakka/baenggipyo/main/docs/logo.svg" alt="baenggipyo (뱅기표)" width="320">
</p>

네이버 항공권·스카이스캐너에서 지금 살 수 있는 항공권과 날짜별 최저가를 조회하는 LLM 에이전트용 CLI.
Korean flight search CLI for LLM agents: search bookable fares, daily lowest prices and destinations on Naver Flights and Skyscanner.

> **뱅기표**: 비행기표를 편하게 부르는 말.

네이버 항공권·스카이스캐너에서 **지금 살 수 있는 항공권**을 조회하는 CLI. Claude, ChatGPT 같은 LLM 에이전트가 항공권을 찾을 때 셸로 호출하는 도구로 쓰려고 만들었다.

![Claude Code 가 baenggipyo guide 를 읽고 네이버 항공권에서 인천→오사카 편도 최저가 3편을 조회해 표로 정리하는 터미널 데모](https://raw.githubusercontent.com/Rukkakka/baenggipyo/main/docs/demo/claude.gif)

Claude Code 에 "11월 20일 인천에서 오사카 가는 편도 항공권 중 제일 싼 거 3개 찾아줘" 라고 시킨 실제 화면이다. Claude 가 `baenggipyo guide` 를 읽고 명령을 골라 조회한 뒤 결과를 표로 정리한다. **운임은 녹화한 날 네이버 항공권을 실제로 조회한 값이라 지금과 다르다** ([데모 영상](https://github.com/Rukkakka/baenggipyo#데모-영상)). 에이전트에 연결하는 방법은 [LLM 에이전트에 연결하기](https://github.com/Rukkakka/baenggipyo#llm-에이전트에-연결하기)에 있다.

> 두 소스 모두 국제선·국내선을 찾는다 (네이버 국내선은 편도·왕복만). 스카이스캐너는 봇 차단이 있어 몇 시간씩 막힐 수 있다. 막히면 30분(거듭되면 최대 4시간) 동안 쓰지 않으니 그동안 네이버를 쓴다.

> 비공식 API 를 쓴다. 스펙이 예고 없이 바뀔 수 있고, 자동수집은 각 서비스 약관과 상충할 소지가 있으니 개인 조회 용도로만 쓴다.

## 왜 만들었나

LLM 에게 항공권을 찾아 달라고 하면 엉뚱한 걸 찾아올 때가 많다. 웹 검색에 걸린 블로그·기사 속 지난 특가나 이미 팔린 운임을 지금 가격처럼 소개하는 식이다. 그래서 LLM 이 검색 결과를 짐작하지 않고 항공권 사이트에서 직접 조회하도록 이 도구를 만들었다. 뱅기표는 지금 살 수 있는 항공권만 공통 JSON 형식과 원 단위로 돌려준다. 예약·결제는 결과의 `url` 에서 한다.

## 원칙: 로그인 없는 기능만

뱅기표는 **로그인하지 않은 사람도 사이트에서 볼 수 있는 정보만** 제공한다. 계정·쿠키·API 키를 받지 않고, 로그인이 필요한 API 는 쓰지 않는다. 스카이스캐너의 공식 파트너 API 도 API 키가 필요해 쓰지 않는다.

그래서 각 사이트에 있더라도 다음 기능은 넣지 않는다.

- 회원가·로그인 할인가, 찜한 항공권, 최근 검색, 가격 알림처럼 로그인해야 쓰는 기능
- 예약·결제·문의처럼 사용자를 대신해 무언가를 보내는 기능 (승객 정보도 받지 않는다)

새 API 를 추가할 때도 로그인 없이 호출되는지 먼저 확인하고, 로그인이 필요하면 추가하지 않는다.

## 설치

macOS·Linux·Windows 에서 쓸 수 있다. 운영체제마다 명령이 다른 곳은 나눠 적었다. Windows 명령은 PowerShell 기준이고 (명령 프롬프트 cmd 는 아님), WSL 안에서 쓴다면 Linux 를 따른다.

![uv 로 baenggipyo 를 설치하고 네이버 항공권에서 인천→간사이 편도 최저가 3편을 직접 조회하는 터미널 데모](https://raw.githubusercontent.com/Rukkakka/baenggipyo/main/docs/demo/demo.gif)

uv 로 설치하고 직접 조회해 본 장면이다. 설치는 실제 PyPI 설치 화면이고, 조회 결과도 녹화한 날 네이버 항공권의 실제 운임이다 ([데모 영상](https://github.com/Rukkakka/baenggipyo#데모-영상)).

### 1. 준비물

- **Python 3.11 이상** — 아래 A(uv)로 설치하면 없어도 된다. uv 가 알아서 받아 쓴다
- **git** — PyPI 로 설치하면 필요 없다. 아직 릴리스하지 않은 최신 수정을 GitHub 에서 받을 때만 쓴다
- 로그인·API 키는 필요 없다

| 운영체제 | Python 버전 확인 | Python 설치 | git 설치 |
|---|---|---|---|
| macOS | `python3 --version` | `brew install python@3.11`, `pyenv install 3.11`, [python.org](https://www.python.org/downloads/) 중 하나 | `xcode-select --install` 또는 `brew install git` |
| Linux | `python3 --version` | Ubuntu·Debian `sudo apt install python3 python3-venv`, Fedora `sudo dnf install python3` | `sudo apt install git` / `sudo dnf install git` |
| Windows | `py --version` | `winget install Python.Python.3.13` 또는 [python.org](https://www.python.org/downloads/) 설치 파일 | `winget install Git.Git` |

- Linux 배포판에 들어 있는 Python 이 3.11 보다 낮으면 (예: Ubuntu 22.04 는 3.10) 따로 올리지 말고 uv 로 설치한다
- Windows 에서 `python3` 를 치면 Microsoft Store 가 열릴 수 있다. python.org·winget 으로 설치했으면 `py` 를 쓴다

### 2. `baenggipyo` 명령 설치

어느 디렉터리에서든 `baenggipyo` 를 부를 수 있도록 전역 명령으로 설치한다. 셋 중 하나만 하면 된다. 도구 전용 가상환경을 따로 만들어 주는 **uv** 나 **pipx** 를 권장한다.

**A. uv (권장)**

uv 가 없으면 먼저 설치한다. 설치 뒤 `uv` 를 못 찾으면 새 터미널을 연다.

```bash
# macOS·Linux
curl -LsSf https://astral.sh/uv/install.sh | sh     # macOS 는 brew install uv 도 된다
```

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"   # 또는 winget install astral-sh.uv
```

그다음은 모든 운영체제에서 같다.

```bash
uv tool install baenggipyo
uv tool update-shell     # ~/.local/bin 을 PATH 에 추가 (처음 한 번). 새 터미널을 연다
```

uv 는 Python 3.11 이 없으면 알아서 받아 쓴다. 설치하지 않고 한 번 써 보려면 `uvx baenggipyo --help` 처럼 `uvx` 로 부른다.

**B. pipx**

pipx 를 설치하고 PATH 를 설정한다 (처음 한 번). 끝나면 새 터미널을 연다.

```bash
# macOS
brew install pipx
# Linux: Ubuntu 23.04 이상·Debian 12 이상은 sudo apt install pipx, Fedora 는 sudo dnf install pipx

pipx ensurepath
```

```powershell
# Windows (PowerShell)
py -m pip install --user pipx
py -m pipx ensurepath
```

그다음은 모든 운영체제에서 같다.

```bash
pipx install baenggipyo
# 기본 Python 이 3.11 미만이면: pipx install --python python3.11 baenggipyo
```

**C. pip + 가상환경**

```bash
# macOS·Linux
python3 -m venv ~/.venvs/baenggipyo        # python3 가 3.11 미만이면 python3.11 처럼 버전을 붙인다
~/.venvs/baenggipyo/bin/pip install baenggipyo

# 매번 경로를 치지 않으려면 PATH 에 링크
mkdir -p ~/.local/bin && ln -s ~/.venvs/baenggipyo/bin/baenggipyo ~/.local/bin/baenggipyo
```

```powershell
# Windows (PowerShell) — bin 대신 Scripts 폴더를 쓴다
py -m venv $HOME\.venvs\baenggipyo
& "$HOME\.venvs\baenggipyo\Scripts\pip.exe" install baenggipyo

& "$HOME\.venvs\baenggipyo\Scripts\baenggipyo.exe" --help    # 실행할 때도 전체 경로로 부른다
```

Windows 는 링크를 만들려면 관리자 권한이 필요하다. `baenggipyo` 만 쳐서 쓰고 싶으면 A·B 가 간단하다.

시스템 Python 에 `pip install` 하지 않는다 (macOS·최근 Linux 는 `externally-managed-environment` 로 막는다). Debian·Ubuntu 에서 `venv` 가 없다는 오류가 나면 `sudo apt install python3-venv` 를 먼저 한다.

**저장소를 이미 받아 두었다면** URL 대신 경로를 넘긴다: `uv tool install .` / `pipx install .` / `pip install .`

**특정 버전을 설치하려면** 버전을 붙인다: `uv tool install baenggipyo==0.1.0` (pipx·pip 도 `baenggipyo==0.1.0`). 버전을 붙여 설치하면 `upgrade` 해도 그 버전에 머무니, 최신으로 돌아가려면 버전 없이 다시 설치한다 (이미 설치돼 있다고 하면 `--force` 를 붙인다).

**아직 릴리스하지 않은 최신 수정을 받으려면** PyPI 대신 GitHub 주소로 설치한다: `uv tool install git+https://github.com/Rukkakka/baenggipyo` (pipx·pip 도 같은 주소). 이렇게 설치하면 `upgrade` 할 때 `main` 의 최신 커밋을 받고, `baenggipyo --version` 이 설치한 커밋을 함께 보여 준다. 버전마다 바뀐 점은 [변경 기록](https://github.com/Rukkakka/baenggipyo/blob/main/CHANGELOG.md)에 있다.

### 3. 설치 확인

명령은 모든 운영체제에서 같다.

```bash
baenggipyo --help             # 명령 목록이 나오면 성공
baenggipyo --version          # 설치한 버전, GitHub 주소로 설치했으면 커밋도 (텍스트)
baenggipyo limits             # 요청 제한 상태 JSON. 네트워크 요청은 보내지 않는다
baenggipyo guide              # LLM 에이전트용 사용 가이드 (텍스트)
baenggipyo naver search airports 오사카   # 실제 조회 한 번
```

`baenggipyo: command not found` (Windows 는 `'baenggipyo' 용어가 ... 인식되지 않습니다`) 가 나오면 PATH 문제다. 먼저 새 터미널을 연다. 그래도 안 되면 `baenggipyo` 가 설치된 `~/.local/bin` (Windows 는 `C:\Users\<이름>\.local\bin`) 이 PATH 에 있는지 본다.

| 운영체제 | PATH 확인 | 없으면 추가 |
|---|---|---|
| macOS (기본 셸 zsh) | `echo $PATH` | `~/.zshrc` 에 `export PATH="$HOME/.local/bin:$PATH"` |
| Linux (기본 셸 bash) | `echo $PATH` | `~/.bashrc` 에 `export PATH="$HOME/.local/bin:$PATH"` |
| Windows (PowerShell) | `$env:Path -split ';'` | `uv tool update-shell` 이나 `py -m pipx ensurepath` 를 다시 실행. 또는 시작 메뉴에서 "환경 변수" 를 검색해 사용자 `Path` 에 추가 |

탭 자동완성이 필요하면 `baenggipyo --install-completion` 을 한 번 실행한다. bash·zsh·fish·PowerShell 을 지원하고 cmd 는 지원하지 않는다.

### 4. Windows 에서 쓸 때

macOS·Linux 는 따로 할 것이 없다. Windows 는 UTF-8 출력 설정을 처음 한 번 챙긴다.

- **UTF-8 출력 설정** — Windows 의 Python 은 출력 인코딩이 기본으로 UTF-8 이 아니다 (한국어 Windows 는 cp949). LLM 에이전트가 baenggipyo 를 부르면 출력이 파이프로 넘어가는데, 이때 한글이 깨지거나 cp949 에 없는 문자(이모지 등)에서 오류가 날 수 있다. 아래처럼 사용자 환경 변수를 설정하고 새 터미널을 연다.

  ```powershell
  setx PYTHONUTF8 1
  ```

### 5. 업데이트 · 삭제

| 설치 방법 | 업데이트 | 삭제 |
|---|---|---|
| uv | `uv tool upgrade baenggipyo` | `uv tool uninstall baenggipyo` |
| pipx | `pipx upgrade baenggipyo` | `pipx uninstall baenggipyo` |
| pip (macOS·Linux) | `~/.venvs/baenggipyo/bin/pip install -U baenggipyo` | `rm -rf ~/.venvs/baenggipyo ~/.local/bin/baenggipyo` |
| pip (Windows) | `& "$HOME\.venvs\baenggipyo\Scripts\pip.exe" install -U baenggipyo` | `Remove-Item -Recurse -Force $HOME\.venvs\baenggipyo` |

GitHub 주소로 설치했다면 uv·pipx 의 `upgrade` 는 그 주소에서 그대로 받고, pip 은 `pip install -U --force-reinstall git+https://github.com/Rukkakka/baenggipyo` 로 받는다.

요청 제한 상태 파일과 네이버 판매처 이름표(하루 동안 두고 다시 쓴다)는 삭제해도 남으니 완전히 지우려면 그 디렉터리도 삭제한다. `BAENGGIPYO_STATE_DIR` 로 위치를 바꿨다면 그 디렉터리다 (절대 경로일 때만 쓴다).

| 운영체제 | 상태 파일 위치 | 삭제 |
|---|---|---|
| macOS·Linux | `~/.cache/baenggipyo` (`XDG_CACHE_HOME` 을 절대 경로로 설정했으면 `$XDG_CACHE_HOME/baenggipyo`) | `rm -rf ~/.cache/baenggipyo` |
| Windows | `C:\Users\<이름>\.cache\baenggipyo` | `Remove-Item -Recurse -Force $HOME\.cache\baenggipyo` |


## LLM 에이전트에 연결하기

에이전트에게는 사용 문서 전체 대신 [LLM 사용 가이드](https://github.com/Rukkakka/baenggipyo/blob/main/docs/llm.md) 하나만 먼저 읽게 한다. 명령 고르는 법, 출력 읽는 법, 에러 대응을 짧게 모아 두었고 나머지는 필요한 절만 찾아 읽게 링크했다. 설치본에 들어 있어 `baenggipyo guide` 로 바로 출력된다 (설치한 버전과 내용이 맞고 네트워크도 쓰지 않는다). 에이전트 지침 파일(`CLAUDE.md`, `AGENTS.md` 등)에 이렇게 적어 두면 된다.

```text
항공권을 찾을 때는 baenggipyo CLI 를 쓴다. 처음 쓰기 전에 `baenggipyo guide` 를 실행해 읽고 그 규칙을 따른다.
```

[맨 위 영상](https://github.com/Rukkakka/baenggipyo#readme)이 이 한 줄을 적어 둔 폴더에서 Claude Code 에 항공권을 찾게 한 장면이다. 지침 파일의 두 번째 줄("(README 데모) 스카이스캐너는 쓰지 않고 네이버 항공권만 쓴다.")은 데모용이다.

## 데모 영상

두 영상의 운임은 모두 2026-10-14 에 네이버 항공권을 실제로 조회한 결과다. 항공권 가격은 계속 바뀌므로 지금 같은 조건으로 조회하면 다른 값이 나온다. 스카이스캐너는 봇 차단이 있어 데모에서 부르지 않았다.

설치 장면은 실제 PyPI 설치 화면이고, 화면에 들어가도록 `jq` 로 항공사·출발 시각·총액만 뽑았다 ([녹화 대본](https://github.com/Rukkakka/baenggipyo/blob/main/scripts/demo/demo.tape)). Claude 의 답은 실제 Claude Code 가 쓴 것이고 ([녹화 대본](https://github.com/Rukkakka/baenggipyo/blob/main/scripts/demo/claude.tape)), 배너의 요금제 글자는 녹화한 계정 정보라 가렸다. Claude Code 는 auto mode 로 녹화했다 (화면 아래 `auto mode on`). 다른 권한 모드에서는 Claude 가 명령을 실행하기 전에 승인을 물을 수 있다.

## 사용 문서

- [LLM 사용 가이드](https://github.com/Rukkakka/baenggipyo/blob/main/docs/llm.md): 에이전트용 요약. 처음 읽을 문서
- [공통 사용 규칙](https://github.com/Rukkakka/baenggipyo/blob/main/docs/common.md): JSON 출력, 금액·시각·누락값, 오류 대응, 요청 제한과 `limits`
- [네이버 항공권 CLI 사용법](https://github.com/Rukkakka/baenggipyo/blob/main/docs/naver.md)
- [스카이스캐너 CLI 사용법](https://github.com/Rukkakka/baenggipyo/blob/main/docs/skyscanner.md)
- [변경 기록](https://github.com/Rukkakka/baenggipyo/blob/main/CHANGELOG.md): 버전별로 바뀐 점

## 개발 문서

- [공통 개발 규약](https://github.com/Rukkakka/baenggipyo/blob/main/AGENTS.md) · [개발 환경](https://github.com/Rukkakka/baenggipyo/blob/main/references/development.md) · [버전·릴리스](https://github.com/Rukkakka/baenggipyo/blob/main/references/release.md)
- [네이버 개발 가이드](https://github.com/Rukkakka/baenggipyo/blob/main/references/providers/naver.md)
- [스카이스캐너 개발 가이드](https://github.com/Rukkakka/baenggipyo/blob/main/references/providers/skyscanner.md)

## 라이선스

[MIT](https://github.com/Rukkakka/baenggipyo/blob/main/LICENSE)
