<p align="center">
  <img src="https://raw.githubusercontent.com/Rukkakka/baenggipyo/main/docs/logo.svg" alt="baenggipyo (뱅기표)" width="320">
</p>

<p align="center">
  <a href="https://pypi.org/project/baenggipyo/"><img src="https://img.shields.io/pypi/v/baenggipyo" alt="PyPI"></a>
  <a href="https://pypi.org/project/baenggipyo/"><img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+"></a>
  <a href="https://github.com/Rukkakka/baenggipyo/actions/workflows/ci.yml"><img src="https://github.com/Rukkakka/baenggipyo/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/Rukkakka/baenggipyo/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
</p>

<p align="center">
  네이버 항공권·스카이스캐너에서 지금 살 수 있는 항공권과 날짜별 최저가를 조회하는 LLM 에이전트용 CLI.<br>
  Korean flight search CLI for LLM agents: search bookable fares, daily lowest prices and destinations on Naver Flights and Skyscanner.
</p>

<p align="center">
  <a href="https://github.com/Rukkakka/baenggipyo#기능">기능</a> ·
  <a href="https://github.com/Rukkakka/baenggipyo#빠른-시작">빠른 시작</a> ·
  <a href="https://github.com/Rukkakka/baenggipyo#설치">설치</a> ·
  <a href="https://github.com/Rukkakka/baenggipyo#llm-에이전트에-연결하기">LLM 에이전트에 연결하기</a> ·
  <a href="https://github.com/Rukkakka/baenggipyo#문서">문서</a>
</p>

> **뱅기표**: 비행기표를 편하게 부르는 말.

Claude, ChatGPT 같은 LLM 에이전트가 항공권을 찾을 때 셸로 호출하는 도구로 쓰려고 만들었다. 조회 결과는 두 소스 모두 같은 JSON 형식과 원 단위로 돌려주고, 예약·결제는 결과의 `url` 에서 한다.

![Claude Code 가 baenggipyo guide 를 읽고 네이버 항공권에서 인천→오사카 편도 최저가 3편을 조회해 표로 정리하는 터미널 데모](https://raw.githubusercontent.com/Rukkakka/baenggipyo/main/docs/demo/claude.gif)

Claude Code 에 "11월 20일 인천에서 오사카 가는 편도 항공권 중 제일 싼 거 3개 찾아줘" 라고 시킨 실제 화면이다. Claude 가 `baenggipyo guide` 를 읽고 명령을 골라 조회한 뒤 결과를 표로 정리한다. **운임은 녹화한 날 네이버 항공권을 실제로 조회한 값이라 지금과 다르다** ([데모 영상](https://github.com/Rukkakka/baenggipyo#데모-영상)). 에이전트에 연결하는 방법은 [LLM 에이전트에 연결하기](https://github.com/Rukkakka/baenggipyo#llm-에이전트에-연결하기)에 있다.

> 두 소스 모두 국제선·국내선을 찾는다 (네이버 국내선은 편도·왕복만). 스카이스캐너는 봇 차단이 있어 몇 시간씩 막힐 수 있다. 막히면 30분(거듭되면 최대 4시간) 동안 쓰지 않으니 그동안 네이버를 쓴다.

> 비공식 API 를 쓴다. 스펙이 예고 없이 바뀔 수 있고, 자동수집은 각 서비스 약관과 상충할 소지가 있으니 개인 조회 용도로만 쓴다.

## 기능

`<소스>` 는 `naver` 또는 `skyscanner` 다. 두 소스가 같은 명령·같은 출력 형식을 쓴다.

| 명령 | 하는 일 |
| --- | --- |
| `<소스> search airports <이름>` | 공항·도시 IATA 코드 찾기 |
| `<소스> search flights <출발> <도착>` | 편도·왕복·다구간 항공권. **지금 살 수 있는 운임만** |
| `<소스> flight <id>` | 그 항공권의 판매처별 가격과 예약 링크 |
| `<소스> calendar <출발> <도착> --month` | 한 달의 날짜별 최저가 (추정가) |
| `<소스> explore <출발> --month` | 목적지 미정: 어디가 싼지 (추정가) |
| `limits` · `guide` | 요청 제한 상태 · LLM 사용 가이드 (네트워크 요청 없음) |

- **로그인·API 키·설정 파일이 없다.** 로그인하지 않은 사람이 사이트에서 볼 수 있는 정보만 조회한다 ([원칙](https://github.com/Rukkakka/baenggipyo#원칙-로그인-없는-기능만))
- **금액은 전부 원 정수**(`*_krw`), **시각은 각 공항 현지 시각**(`YYYY-MM-DDTHH:MM`), 소요 시간은 분이다
- 매진·만료된 운임은 내보내지 않고, 가격을 캐시하지 않는다
- 조회 범위·제한·조건 해석은 결과의 `notes` 로 알린다
- `--brief`·`--limit`·`--offer-urls` 로 응답을 줄여 에이전트의 토큰을 아낀다
- 모든 요청이 소스별 요청 제한을 지나고, 그 상태를 모든 baenggipyo 프로세스가 함께 쓴다

## 빠른 시작

```bash
uv tool install baenggipyo        # 또는 pipx install baenggipyo
baenggipyo naver search flights ICN KIX --depart 2026-11-20 --limit 2 --brief
```

![uv 로 baenggipyo 를 설치하고 네이버 항공권에서 인천→간사이 편도 최저가 3편을 직접 조회하는 터미널 데모](https://raw.githubusercontent.com/Rukkakka/baenggipyo/main/docs/demo/demo.gif)

결과는 stdout 에 JSON 하나로 나온다 (에러는 stderr 에 JSON). 아래는 위 명령의 실제 출력에서 두 번째 항공권과 일부 키를 줄인 것이다 (2026-10-20 조회).

```json
{
  "notice": "가격은 조회 시점 기준이며 예약 단계에서 바뀔 수 있습니다. 예약·결제는 각 url 의 원 사이트에서 하세요. baenggipyo 는 탐색용입니다.",
  "notes": ["조건에 맞는 항공권이 더 있을 수 있습니다. 정렬 순서 앞의 2개만 받았습니다 (--limit 최대 50)"],
  "search": {
    "trip_type": "편도",
    "legs": [{ "origin": "ICN", "destination": "KIX", "date": "2026-11-20" }],
    "cabin": "일반석",
    "adults": 1,
    "url": "https://flight.naver.com/flights/international/ICN:airport-KIX:airport-20261120?adult=1&fareType=Y"
  },
  "returned": 2,
  "itineraries": [
    {
      "id": "Y-100-0-ICNaKIXa20261120~20261120ICNKIX7C1393",
      "journeys": [
        {
          "departure_airport": "ICN",
          "arrival_airport": "KIX",
          "departure_at": "2026-11-20T14:10",
          "arrival_at": "2026-11-20T16:00",
          "duration_minutes": 110,
          "stops": 0,
          "stopover_airports": [],
          "segments": [{ "airline": "제주항공", "flight_number": "7C1393" }]
        }
      ],
      "total_price_krw": 167818,
      "price_per_adult_krw": 167818,
      "offer_count": 22,
      "cheapest_seller": null,
      "baggage_included": null,
      "card_discount_price_krw": 167400,
      "card_discount_name": "성인/하나카드(이용실적 충족시)"
    }
  ]
}
```

이어서 `baenggipyo naver flight <id>` 로 판매처별 가격과 예약 링크를 본다. 날짜를 정하지 않았으면 `calendar` 로 싼 날을, 목적지를 정하지 않았으면 `explore` 로 싼 곳을 먼저 고른다. 명령별 옵션과 반환 필드는 [네이버](https://github.com/Rukkakka/baenggipyo/blob/main/docs/naver.md)·[스카이스캐너](https://github.com/Rukkakka/baenggipyo/blob/main/docs/skyscanner.md) 사용법에 있다.

## 설치

Python 3.11 이상이 필요하고 (uv 로 설치하면 없어도 된다), macOS·Linux·Windows 에서 쓸 수 있다. 셋 중 하나만 하면 된다.

| 방법 | 설치 | 업데이트 |
| --- | --- | --- |
| **uv** (권장) | `uv tool install baenggipyo` | `uv tool upgrade baenggipyo` |
| pipx | `pipx install baenggipyo` | `pipx upgrade baenggipyo` |
| pip + 가상환경 | `python3 -m venv ~/.venvs/baenggipyo && ~/.venvs/baenggipyo/bin/pip install baenggipyo` | `~/.venvs/baenggipyo/bin/pip install -U baenggipyo` |

```bash
baenggipyo --help      # 명령 목록이 나오면 성공
baenggipyo limits      # 요청 제한 상태 JSON. 네트워크 요청은 보내지 않는다
```

설치하지 않고 한 번 써 보려면 `uvx baenggipyo --help` 로 부른다. 아직 릴리스하지 않은 최신 수정은 `uv tool install git+https://github.com/Rukkakka/baenggipyo` 로 받는다.

**준비물(Python·git) 설치, Windows·PowerShell 명령, `command not found` 일 때 PATH 설정, 특정 버전 설치, 삭제**는 [설치 문서](https://github.com/Rukkakka/baenggipyo/blob/main/docs/install.md)에 있다. 버전마다 바뀐 점은 [변경 기록](https://github.com/Rukkakka/baenggipyo/blob/main/CHANGELOG.md)에 있다.

## LLM 에이전트에 연결하기

에이전트에게는 사용 문서 전체 대신 [LLM 사용 가이드](https://github.com/Rukkakka/baenggipyo/blob/main/docs/llm.md) 하나만 먼저 읽게 한다. 명령 고르는 법, 출력 읽는 법, 에러 대응을 짧게 모아 두었고 나머지는 필요한 절만 찾아 읽게 링크했다. 설치본에 들어 있어 `baenggipyo guide` 로 바로 출력된다 (설치한 버전과 내용이 맞고 네트워크도 쓰지 않는다). 에이전트 지침 파일(`CLAUDE.md`, `AGENTS.md` 등)에 이렇게 적어 두면 된다.

```text
항공권을 찾을 때는 baenggipyo CLI 를 쓴다. 처음 쓰기 전에 `baenggipyo guide` 를 실행해 읽고 그 규칙을 따른다.
```

[맨 위 영상](https://github.com/Rukkakka/baenggipyo#readme)이 이 한 줄을 적어 둔 폴더에서 Claude Code 에 항공권을 찾게 한 장면이다. 지침 파일의 두 번째 줄("(README 데모) 스카이스캐너는 쓰지 않고 네이버 항공권만 쓴다.")은 데모용이다.

## 설정

로그인·API 키·설정 파일은 없다. 환경 변수 세 개만 쓴다.

| 환경 변수 | 하는 일 |
| --- | --- |
| `BAENGGIPYO_STATE_DIR` | 요청 제한 상태와 네이버 판매처 이름표를 둘 디렉터리. 기본은 `~/.cache/baenggipyo`. 절대 경로가 아니면 무시한다 |
| `XDG_CACHE_HOME` | 절대 경로로 설정해 두었으면 그 아래 `baenggipyo` 를 쓴다 (`BAENGGIPYO_STATE_DIR` 이 우선) |
| `PYTHONUTF8` | Windows 에서 `1` 로 설정한다 (아래) |

### Windows 에서 쓸 때

Windows 의 Python 은 출력 인코딩이 기본으로 UTF-8 이 아니다 (한국어 Windows 는 cp949). LLM 에이전트가 baenggipyo 를 부르면 출력이 파이프로 넘어가는데, 이때 한글이 깨지거나 cp949 에 없는 문자(이모지 등)에서 오류가 날 수 있다. 아래처럼 사용자 환경 변수를 설정하고 새 터미널을 연다. macOS·Linux 는 따로 할 것이 없다.

```powershell
setx PYTHONUTF8 1
```

## 왜 만들었나

LLM 에게 항공권을 찾아 달라고 하면 엉뚱한 걸 찾아올 때가 많다. 웹 검색에 걸린 블로그·기사 속 지난 특가나 이미 팔린 운임을 지금 가격처럼 소개하는 식이다. 그래서 LLM 이 검색 결과를 짐작하지 않고 항공권 사이트에서 직접 조회하도록 이 도구를 만들었다. 뱅기표는 지금 살 수 있는 항공권만 공통 JSON 형식과 원 단위로 돌려준다. 예약·결제는 결과의 `url` 에서 한다.

## 원칙: 로그인 없는 기능만

뱅기표는 **로그인하지 않은 사람도 사이트에서 볼 수 있는 정보만** 제공한다. 계정·쿠키·API 키를 받지 않고, 로그인이 필요한 API 는 쓰지 않는다. 스카이스캐너의 공식 파트너 API 도 API 키가 필요해 쓰지 않는다.

그래서 각 사이트에 있더라도 다음 기능은 넣지 않는다.

- 회원가·로그인 할인가, 찜한 항공권, 최근 검색, 가격 알림처럼 로그인해야 쓰는 기능
- 예약·결제·문의처럼 사용자를 대신해 무언가를 보내는 기능 (승객 정보도 받지 않는다)

새 API 를 추가할 때도 로그인 없이 호출되는지 먼저 확인하고, 로그인이 필요하면 추가하지 않는다.

## 데모 영상

두 영상의 운임은 모두 2026-10-14 에 네이버 항공권을 실제로 조회한 결과다. 항공권 가격은 계속 바뀌므로 지금 같은 조건으로 조회하면 다른 값이 나온다. 스카이스캐너는 봇 차단이 있어 데모에서 부르지 않았다.

설치 장면은 실제 PyPI 설치 화면이고, 화면에 들어가도록 `jq` 로 항공사·출발 시각·총액만 뽑았다 ([녹화 대본](https://github.com/Rukkakka/baenggipyo/blob/main/scripts/demo/demo.tape)). Claude 의 답은 실제 Claude Code 가 쓴 것이고 ([녹화 대본](https://github.com/Rukkakka/baenggipyo/blob/main/scripts/demo/claude.tape)), 배너의 요금제 글자는 녹화한 계정 정보라 가렸다. Claude Code 는 auto mode 로 녹화했다 (화면 아래 `auto mode on`). 다른 권한 모드에서는 Claude 가 명령을 실행하기 전에 승인을 물을 수 있다.

## 문서

사용 문서

- [LLM 사용 가이드](https://github.com/Rukkakka/baenggipyo/blob/main/docs/llm.md): 에이전트용 요약. 처음 읽을 문서
- [설치](https://github.com/Rukkakka/baenggipyo/blob/main/docs/install.md): 준비물, 설치 방법 세 가지, PATH, 업데이트·삭제
- [공통 사용 규칙](https://github.com/Rukkakka/baenggipyo/blob/main/docs/common.md): JSON 출력, 금액·시각·누락값, 오류 대응, 요청 제한과 `limits`
- [네이버 항공권 CLI 사용법](https://github.com/Rukkakka/baenggipyo/blob/main/docs/naver.md)
- [스카이스캐너 CLI 사용법](https://github.com/Rukkakka/baenggipyo/blob/main/docs/skyscanner.md)
- [변경 기록](https://github.com/Rukkakka/baenggipyo/blob/main/CHANGELOG.md): 버전별로 바뀐 점

개발 문서

- [공통 개발 규약](https://github.com/Rukkakka/baenggipyo/blob/main/AGENTS.md) · [개발 환경](https://github.com/Rukkakka/baenggipyo/blob/main/references/development.md) · [버전·릴리스](https://github.com/Rukkakka/baenggipyo/blob/main/references/release.md)
- [네이버 개발 가이드](https://github.com/Rukkakka/baenggipyo/blob/main/references/providers/naver.md)
- [스카이스캐너 개발 가이드](https://github.com/Rukkakka/baenggipyo/blob/main/references/providers/skyscanner.md)

## 기여

버그·기능 제안은 [이슈](https://github.com/Rukkakka/baenggipyo/issues/new/choose)로 열고, 코드를 고치기 전에 [개발 규약](https://github.com/Rukkakka/baenggipyo/blob/main/AGENTS.md)의 "꼭 지킬 것"(로그인 없는 기능만, 출력 규약, 봇 차단 우회 금지 등)을 읽는다. 개발 환경 설치와 검사는 [개발 환경](https://github.com/Rukkakka/baenggipyo/blob/main/references/development.md), 이슈 → 브랜치 → 커밋 → PR 절차는 [작업 절차](https://github.com/Rukkakka/baenggipyo/blob/main/references/workflow.md)에 있다. 모든 변경은 PR 로 들어가고 CI(ruff·mypy·pytest·설치 확인)를 통과해야 한다.

## 라이선스

[MIT](https://github.com/Rukkakka/baenggipyo/blob/main/LICENSE)
