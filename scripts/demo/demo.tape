# README 데모 녹화 대본 (VHS, https://github.com/charmbracelet/vhs).
# 저장소 루트에서 `vhs scripts/demo/demo.tape` 로 다시 찍는다. uv·jq 가 필요하다. 화면 설정과 숨긴 준비는 setup.tape 에 있다.
#
# 설치 장면은 PyPI 에서 진짜로 설치한다 (임시 폴더에 설치해 쓰던 baenggipyo 는 건드리지 않는다).
# 조회 장면도 네이버 항공권을 실제로 조회한다. 결과는 녹화한 날의 실제 운임이라 다시 찍으면 달라지고, 출발일(--depart)은
# 녹화하는 날보다 뒤로 바꾼다. 실제 요청이라 요청 제한 상태 디렉터리는 바꾸지 않고 쓰던 baenggipyo 와 공유한다.
# 스카이스캐너는 봇 차단이 있어 데모에서 부르지 않는다.
# 한 건이 60줄쯤이라 화면에 들어가도록 jq 로 항공사·출발 시각·총액만 뽑는다.

Output docs/demo/demo.gif

Require uv
Require jq

Source scripts/demo/setup.tape

Type "clear" Enter
Sleep 500ms
Show

Type "uv tool install baenggipyo"
Sleep 600ms
Enter
Wait+Screen /Installed 1 executable/
Sleep 2s

Type "baenggipyo --version"
Sleep 400ms
Enter
# 설치 직후 첫 실행은 import 가 느려 출력이 뜰 때까지 기다린다
Wait+Screen /baenggipyo \d/
Sleep 2s

Hide
Type "clear" Enter
Sleep 300ms
Show

Type `baenggipyo naver search flights ICN KIX --depart 2026-11-20 --limit 3 | jq '.itineraries[] | {airline: .journeys[0].segments[0].airline, departure_at: .journeys[0].departure_at, total_price_krw}'`
Sleep 600ms
Enter
# 네이버 국제선 검색은 10~20초 걸린다. 입력한 명령에도 total_price_krw 가 있어, 결과에만 나오는 '"total_price_krw": 숫자' 를 기다린다
Wait+Screen /"total_price_krw": \d/
Sleep 6s

Hide
Type `rm -rf "$T"` Enter
# 녹화가 끝나기 전에 지우기가 끝나도록 프롬프트가 돌아오기를 기다린다
Wait /\$ ?$/
