# demo.tape·claude.tape 가 맨 앞에서 `Source` 로 불러 쓰는 공통 화면 설정과 숨긴 준비. 혼자 돌리지 않는다.
# 창 크기·글꼴·여백을 바꾸면 hide_plan.py 의 좌표(PLAN_BOX·LOGO_*)도 다시 잰다. 그 값은 이 설정으로 찍은 화면에서 쟀다.
# 한글 고정폭 글꼴 D2Coding 이 필요하다 (`brew install --cask font-d2coding`).

Set Shell zsh
Set FontFamily "D2Coding"
Set FontSize 17
Set Width 1200
Set Height 880
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 45ms
Set WaitTimeout 300s

Env LANG "en_US.UTF-8"

Hide
# 저장소의 .venv 를 켠 채 녹화하면 zsh 가 기억해 둔 .venv 의 개발 설치를 부른다. 숨긴 준비에서 .venv 를 PATH 에서 빼고
# 기억한 명령 위치를 비워, 화면의 baenggipyo 가 PyPI 에서 설치한 것이 되게 한다
Type `unset VIRTUAL_ENV; path=(${path:#*/.venv/bin})` Enter
# baenggipyo 는 임시 폴더($T)에 설치해 쓰던 baenggipyo 를 건드리지 않는다. 각 대본이 녹화 끝에 $T 를 지운다
Type `export T="$(mktemp -d)"` Enter
Type `export UV_TOOL_DIR="$T/tools" UV_TOOL_BIN_DIR="$T/bin" UV_CACHE_DIR="$T/cache" PATH="$T/bin:$PATH" PROMPT='$ '` Enter
Type "hash -r" Enter
