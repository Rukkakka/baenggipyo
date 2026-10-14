# README 'LLM 에이전트에 연결하기' 데모 녹화 대본 (VHS, https://github.com/charmbracelet/vhs).
# 저장소 루트에서 `vhs docs/demo/claude.tape` 로 다시 찍고, `python3 docs/demo/hide_plan.py docs/demo/claude.gif` 로
# 배너의 요금제 글자(녹화한 계정의 요금제)를 덮는다. uv·Claude Code(`claude`, 로그인)·ffmpeg 가 필요하다.
#
# 실제 Claude Code 에 "11월 20일 인천에서 오사카 가는 편도 항공권 중 제일 싼 거 3개 찾아줘" 라고 시킨다. 작업 폴더
# (/tmp/baenggipyo-demo)에는 README 가 권하는 지침 한 줄과 데모용 한 줄(claude-instructions.md → CLAUDE.md), baenggipyo 만
# 허용하는 권한과 녹화가 길어지지 않게 줄인 생각 깊이(claude-settings.json)를 둔다.
# Claude 가 부르는 `baenggipyo` 는 PyPI 에서 임시 폴더에 설치한 진짜 baenggipyo 라 네이버 항공권을 실제로 조회한다. 결과는
# 녹화한 날의 실제 운임이라 다시 찍으면 달라지고, 날짜는 녹화하는 날보다 뒤로 바꾼다. 실제 요청이라 요청 제한 상태 디렉터리는
# 바꾸지 않고 쓰던 baenggipyo 와 공유한다. 스카이스캐너는 봇 차단이 있어 지침의 데모용 한 줄로 쓰지 않게 한다.
# Claude 의 답은 매번 조금씩 달라 깔끔한 장면이 나올 때까지 다시 찍을 수 있다.
# 녹화 전에 숨긴 채로 claude 를 한 번 열어, 처음 여는 폴더면 신뢰 질문에서 'Yes' 를 고르고 닫는다.
# VHS 의 Type 은 한글을 입력하지 못해, 한글 문장은 숨긴 준비에서 zsh 단축키(Ctrl+X 숫자)에 묶어 두고 그 키로 넣는다.
# 한글 고정폭 글꼴 D2Coding 이 필요하다 (`brew install --cask font-d2coding`).

Output docs/demo/claude.gif

Require uv
Require claude

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
# Claude Code 안에서 vhs 를 돌리면 그 세션의 환경 변수(CLAUDE_CODE_CHILD_SESSION 등)가 넘어와, 녹화하는 claude 가 하위 세션 경고를
# 띄운다. 숨긴 준비에서 지운다
Type `unset -m 'CLAUDE*'; unset AI_AGENT` Enter
Type `export DEMO="$PWD/docs/demo" T="$(mktemp -d)" W=/tmp/baenggipyo-demo` Enter
Type `export UV_TOOL_DIR="$T/tools" UV_TOOL_BIN_DIR="$T/bin" UV_CACHE_DIR="$T/cache" PATH="$T/bin:$PATH" PROMPT='$ '` Enter
Type "hash -r" Enter
Type `uv tool install -q baenggipyo` Enter
Type `rm -rf "$W" && mkdir -p "$W/.claude" && cp "$DEMO/claude-instructions.md" "$W/CLAUDE.md" && cp "$DEMO/claude-settings.json" "$W/.claude/settings.json" && cd "$W"` Enter
# Ctrl+X 4 → 11월 20일 인천에서 오사카 가는 편도 항공권 중 제일 싼 거 3개 찾아줘
Type `bindkey -s '^X4' $'11\uc6d4 20\uc77c \uc778\ucc9c\uc5d0\uc11c \uc624\uc0ac\uce74 \uac00\ub294 \ud3b8\ub3c4 \ud56d\uacf5\uad8c \uc911 \uc81c\uc77c \uc2fc \uac70 3\uac1c \ucc3e\uc544\uc918'` Enter
# 폴더 신뢰: 질문이 뜨면 기본값이 'No, exit' 라 아래로 옮겨 고른다. 이미 신뢰한 폴더면 빈 입력창이라 아무 일도 없다
Type "claude" Enter
Wait+Screen /trust this folder|Claude Code v/
Sleep 1s
Down
Enter
Wait+Screen /Claude Code v/
Sleep 1s
Type "/exit"
Sleep 1s
Enter
Sleep 2s
Type "clear" Enter
Sleep 500ms
Show

Type "cat CLAUDE.md"
Sleep 400ms
Enter
Sleep 2s

Type `claude "`
Ctrl+X
Type "4"
Type `"`
Sleep 600ms
Enter
# 답이 끝나면 '… for 30s · done' 줄이 뜬다. 답을 읽을 시간을 둔다
Wait+Screen /\d+s · done/
Sleep 10s

Hide
Ctrl+C
Ctrl+C
Type `cd / && rm -rf "$T" "$W"` Enter
