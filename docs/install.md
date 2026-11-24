# 설치

[README](../README.md) · [공통 사용 규칙](common.md) · [LLM 가이드](llm.md)

> 첫 조회는 [README 의 빠른 시작](../README.md#빠른-시작)에 있다. 이 문서는 한 줄 설치(설치 스크립트)와, 직접 설치할 때의
> 준비물·설치 방법 세 가지, 브라우저(Chromium) 설치, PATH 문제, 업데이트·삭제까지 자세히 적는다.

macOS·Linux·Windows 에서 쓸 수 있다. 운영체제마다 명령이 다른 곳은 나눠 적었다. Windows 명령은 PowerShell 기준이고 (명령 프롬프트 cmd 는 아님), WSL 안에서 쓴다면 Linux 를 따른다.

## 한 줄 설치

처음 설치하는 사람은 이것만 하면 된다. 아래 1~3 절은 직접 설치하려는 사람용이고, 4·5 절(설치 확인, 업데이트·삭제)은 스크립트로 설치했어도 본다.

**Windows**

1. 시작 메뉴에서 `PowerShell` 을 검색해 **Windows PowerShell** 을 연다. 명령 프롬프트(cmd)가 아니다. 줄 앞에 `PS C:\Users\<이름>>` 이 보이면 맞다
2. 아래 줄을 붙여 넣고 Enter 를 누른다

   ```powershell
   irm https://raw.githubusercontent.com/Rukkakka/baenggipyo/main/scripts/install/install.ps1 | iex
   ```

3. 마지막에 버전과 `설치를 마쳤다` 가 나오면 끝이다. **그 창에서 바로** `baenggipyo` 를 쓸 수 있다

설치 전에 열려 있던 다른 창은 PATH 가 예전 그대로라 `baenggipyo` 를 못 찾는다. 그 창은 닫고 새로 연다.

**macOS·Linux**

1. 터미널을 연다 (macOS 는 Spotlight 에서 `터미널` 을 찾는다)
2. 아래 줄을 붙여 넣고 Enter 를 누른다

   ```bash
   curl -LsSf https://raw.githubusercontent.com/Rukkakka/baenggipyo/main/scripts/install/install.sh | sh
   ```

3. 버전과 `설치를 마쳤다` 가 나오면 끝이다. 새로 연 터미널부터 `baenggipyo` 가 잡힌다. 그 창에서 바로 쓰려면 스크립트가 마지막에 보여 주는 `export PATH=…` 줄을 실행한다

**스크립트가 하는 일.** 몇 번을 다시 실행해도 되고, 다시 실행하면 baenggipyo 를 최신 버전으로 올린다.

| 단계 | 하는 일 |
|---|---|
| uv | 없으면 설치한다. Python 이 없어도 uv 가 알아서 받는다 |
| baenggipyo | `uv tool install --upgrade --python '>=3.11,<3.15' baenggipyo`. 보장 범위(3.11~3.14)의 Python 이 있으면 그것을 쓰고, 없으면 uv 가 그 범위에서 받는다 |
| PATH | baenggipyo 가 든 폴더(`~/.local/bin`, Windows 는 `C:\Users\<이름>\.local\bin`)를 사용자 PATH 에 넣는다. Windows 는 지금 창에도 넣는다 |
| 한글 출력 | Windows 만. 사용자 환경 변수 `PYTHONUTF8=1` 을 설정한다 ([왜 필요한가](../README.md#windows-에서-쓸-때)) |
| 브라우저 | `baenggipyo install-browser` 로 Chromium(수백 MB)을 받고 한 번 띄워 본다. 이미 있으면 건너뛴다. `install-browser` 가 없는 PyPI 판(0.8.0 이하)이면 건너뛴다. 리눅스에서 시스템 라이브러리가 없다고 멈추면 안내된 `sudo … install-deps chromium` 을 실행하고 스크립트를 다시 실행한다 |
| 에이전트 지침 | Codex(`~/.codex/AGENTS.md`, `CODEX_HOME` 이 있으면 그 폴더)·Claude Code(`~/.claude/CLAUDE.md`) 설정 폴더가 있으면 [baenggipyo 사용 규칙 한 줄](../README.md#llm-에이전트에-연결하기)을 덧붙인다. 이미 있으면 건너뛴다. 둘 다 없으면 그 한 줄을 화면에 보여 준다 |

브라우저를 받지 못하면 에이전트 지침 단계 전에 멈춘다. 고친 뒤 스크립트를 다시 실행한다 (`install-browser` 만 다시 하면 지침 단계가 빠진다).

**설치 뒤 에이전트에서 쓸 때**

- 에이전트는 baenggipyo 를 설치한 **뒤에 연 창**에서 켠다. 예전 창에서 켠 에이전트는 예전 PATH 를 물려받아 `baenggipyo` 를 못 찾는다
- 지침 한 줄이 없는 에이전트에 "baenggipyo 쓸 수 있어?" 라고만 물으면, 연결된 도구(MCP) 목록만 보고 없다고 답한다. baenggipyo 는 셸 명령이니 "셸에서 `baenggipyo guide` 를 실행해 읽고 …" 처럼 시킨다
- Codex 는 기본 샌드박스가 인터넷과 작업 폴더 밖 접근을 막아, baenggipyo 를 못 찾거나 조회가 막힐 수 있다. Codex 안에서 `/permissions` 로 Full Access 를 고른다. 매번 고르지 않으려면 `~/.codex/config.toml` 에 `sandbox_mode = "danger-full-access"` 를 넣는다

## 1. 준비물

- **Python 3.11~3.14** — baenggipyo 가 현재 보장하는 범위다 (CI 가 세 운영체제에서 확인한 버전, 3.15 는 확인 전). 아래 A(uv)로 설치하면 없어도 된다. uv 가 받아 쓰는데, Python 이 없으면 가장 새 버전을 받으니 `--python 3.14` 를 붙여 범위 안에서 고른다 (한 줄 설치 스크립트는 알아서 고른다)
- **git** — PyPI 로 설치하면 필요 없다. 아직 릴리스하지 않은 최신 수정을 GitHub 에서 받을 때만 쓴다
- 로그인·API 키는 필요 없다

| 운영체제 | Python 버전 확인 | Python 설치 | git 설치 |
|---|---|---|---|
| macOS | `python3 --version` | `brew install python@3.11`, `pyenv install 3.11`, [python.org](https://www.python.org/downloads/) 중 하나 | `xcode-select --install` 또는 `brew install git` |
| Linux | `python3 --version` | Ubuntu·Debian `sudo apt install python3 python3-venv`, Fedora `sudo dnf install python3` | `sudo apt install git` / `sudo dnf install git` |
| Windows | `py --version` | `winget install Python.Python.3.13` 또는 [python.org](https://www.python.org/downloads/) 설치 파일 | `winget install Git.Git` |

- Linux 배포판에 들어 있는 Python 이 3.11 보다 낮으면 (예: Ubuntu 22.04 는 3.10) 따로 올리지 말고 uv 로 설치한다
- Windows 에서 `python3` 를 치면 Microsoft Store 가 열릴 수 있다. python.org·winget 으로 설치했으면 `py` 를 쓴다

## 2. `baenggipyo` 명령 설치

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

uv 는 Python 3.11 이상이 없으면 알아서 받아 쓴다. 이때 가장 새 버전(보장 범위 밖일 수 있다)을 받으니 `uv tool install --python 3.14 baenggipyo` 처럼 고른다. 설치하지 않고 한 번 써 보려면 `uvx baenggipyo --help` 처럼 `uvx` 로 부른다. 조회까지 해 보려면 `uvx baenggipyo install-browser` 를 먼저 한 번 실행한다 ([3. 브라우저 설치](#3-브라우저chromium-설치)).

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

**아직 릴리스하지 않은 최신 수정을 받으려면** PyPI 대신 GitHub 주소로 설치한다: `uv tool install git+https://github.com/Rukkakka/baenggipyo` (pipx·pip 도 같은 주소). 이렇게 설치하면 `upgrade` 할 때 `main` 의 최신 커밋을 받고, `baenggipyo --version` 이 설치한 커밋을 함께 보여 준다. 버전마다 바뀐 점은 [변경 기록](../CHANGELOG.md)에 있다.

## 3. 브라우저(Chromium) 설치

baenggipyo 는 사이트에 요청을 보낼 때 Chromium 브라우저를 띄워 쓴다. Chromium 은 위 설치 명령으로 함께 오지 않으니 **설치한 뒤 한 번 실행한다** (한 줄 설치 스크립트로 설치했으면 스크립트가 대신 했다). 안 하면 조회 명령이 `BROWSER_UNAVAILABLE` 에러로 멈춘다. 조회 명령이 도중에 알아서 받지는 않는다 (받는 동안 에이전트가 기다리다 시간 초과되지 않게).

```bash
baenggipyo install-browser
```

- 수백 MB 를 받는다. 이미 받았으면 다시 받지 않고, Chromium 이 뜨는지 한 번 확인만 한다
- 끝나면 `{"browser", "version", "executable"}` JSON 이 나온다. `executable` 이 받은 Chromium 의 위치다
- **baenggipyo 를 업데이트한 뒤에도 한 번 실행한다.** 새 버전이 다른 Chromium 을 요구할 수 있다
- Linux 는 Chromium 이 쓰는 시스템 라이브러리가 없으면 `BROWSER_UNAVAILABLE` 이 나온다. 에러의 `hint` 에 있는 명령(`sudo <baenggipyo 의 Python 경로> -m patchright install-deps chromium`, 관리자 권한)을 실행한 뒤 `baenggipyo install-browser` 로 다시 확인한다
- 받다가 실패하면 `BROWSER_INSTALL_FAILED` 다. 에러의 `output`·`hint` 에 원인(네트워크·권한)이 있다 ([공통 사용 규칙의 에러 대응](common.md#에러-대응))
- 회사망·프록시·보안 프로그램이 있는 곳에서 쓰면 [프록시와 인증서](common.md#프록시와-인증서)를 본다. 브라우저는 `SSL_CERT_FILE` 을 쓰지 않고, 프록시 환경 변수가 없으면 OS 프록시 설정을 쓴다

브라우저를 띄우느라 조회 명령마다 몇 초 더 걸린다.

## 4. 설치 확인

명령은 모든 운영체제에서 같다.

```bash
baenggipyo --help             # 명령 목록이 나오면 성공
baenggipyo --version          # 설치한 버전, GitHub 주소로 설치했으면 커밋도 (텍스트)
baenggipyo limits             # 요청 제한 상태 JSON. 네트워크 요청은 보내지 않는다
baenggipyo guide              # LLM 에이전트용 사용 가이드 (텍스트). 소스별 주의점은 guide naver / guide skyscanner
baenggipyo naver search airports 오사카   # 실제 조회 한 번. 위 3 의 install-browser 를 먼저 해야 한다
```

`baenggipyo: command not found` (Windows 는 `'baenggipyo' 용어가 ... 인식되지 않습니다`) 가 나오면 PATH 문제다. 먼저 새 터미널을 연다. 그래도 안 되면 `baenggipyo` 가 설치된 `~/.local/bin` (Windows 는 `C:\Users\<이름>\.local\bin`) 이 PATH 에 있는지 본다.

| 운영체제 | PATH 확인 | 없으면 추가 |
|---|---|---|
| macOS (기본 셸 zsh) | `echo $PATH` | `~/.zshrc` 에 `export PATH="$HOME/.local/bin:$PATH"` |
| Linux (기본 셸 bash) | `echo $PATH` | `~/.bashrc` 에 `export PATH="$HOME/.local/bin:$PATH"` |
| Windows (PowerShell) | `$env:Path -split ';'` | `uv tool update-shell` 이나 `py -m pipx ensurepath` 를 다시 실행. 또는 시작 메뉴에서 "환경 변수" 를 검색해 사용자 `Path` 에 추가 |

탭 자동완성이 필요하면 `baenggipyo --install-completion` 을 한 번 실행한다. bash·zsh·fish·PowerShell 을 지원하고 cmd 는 지원하지 않는다.

Windows 는 UTF-8 출력 설정(`setx PYTHONUTF8 1`)을 처음 한 번 챙긴다 ([README 의 Windows 에서 쓸 때](../README.md#windows-에서-쓸-때)). macOS·Linux 는 따로 할 것이 없다.

## 5. 업데이트 · 삭제

| 설치 방법 | 업데이트 | 삭제 |
|---|---|---|
| 설치 스크립트 | 스크립트를 다시 실행하거나 `uv tool upgrade baenggipyo` | `uv tool uninstall baenggipyo` |
| uv | `uv tool upgrade baenggipyo` | `uv tool uninstall baenggipyo` |
| pipx | `pipx upgrade baenggipyo` | `pipx uninstall baenggipyo` |
| pip (macOS·Linux) | `~/.venvs/baenggipyo/bin/pip install -U baenggipyo` | `rm -rf ~/.venvs/baenggipyo ~/.local/bin/baenggipyo` |
| pip (Windows) | `& "$HOME\.venvs\baenggipyo\Scripts\pip.exe" install -U baenggipyo` | `Remove-Item -Recurse -Force $HOME\.venvs\baenggipyo` |

GitHub 주소로 설치했다면 uv·pipx 의 `upgrade` 는 그 주소에서 그대로 받고, pip 은 `pip install -U --force-reinstall git+https://github.com/Rukkakka/baenggipyo` 로 받는다.

업데이트한 뒤에는 `baenggipyo install-browser` 를 한 번 실행한다 (설치 스크립트로 올렸으면 스크립트가 한다. [3. 브라우저 설치](#3-브라우저chromium-설치) 참고).

설치 스크립트가 넣은 PATH 항목, `PYTHONUTF8`, 에이전트 지침 파일의 baenggipyo 한 줄은 삭제해도 남는다. 쓰지 않으면 직접 지운다. uv 를 지우려면 [uv 삭제 안내](https://docs.astral.sh/uv/getting-started/installation/#uninstallation)를 따른다.

요청 제한 상태 파일, 네이버 판매처 이름표(하루 동안 두고 다시 쓴다), 브라우저 쿠키·기록(`browser` 폴더)은 삭제해도 남으니 완전히 지우려면 그 디렉터리도 삭제한다. `BAENGGIPYO_STATE_DIR` 로 위치를 바꿨다면 그 디렉터리다 ([설정](../README.md#설정)).

| 운영체제 | 상태 파일 위치 | 삭제 |
|---|---|---|
| macOS·Linux | `~/.cache/baenggipyo` (`XDG_CACHE_HOME` 을 절대 경로로 설정했으면 `$XDG_CACHE_HOME/baenggipyo`) | `rm -rf ~/.cache/baenggipyo` |
| Windows | `C:\Users\<이름>\.cache\baenggipyo` | `Remove-Item -Recurse -Force $HOME\.cache\baenggipyo` |

`baenggipyo install-browser` 가 받은 Chromium 도 baenggipyo 를 지워도 남는다. 위치는 `install-browser` 출력의 `executable` 이 가리키는 `ms-playwright` 폴더 안의 `chromium-<번호>` 폴더다 (기본은 macOS `~/Library/Caches/ms-playwright`, Linux `~/.cache/ms-playwright`, Windows `C:\Users\<이름>\AppData\Local\ms-playwright`). 그 폴더에는 다른 도구가 받은 브라우저도 있을 수 있으니 `chromium-<번호>` 폴더만 지운다.
