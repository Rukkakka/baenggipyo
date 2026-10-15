# Changelog

baenggipyo 의 버전별 변경 기록. 사용자(에이전트 포함)에게 보이는 변경만 적고, 아직 릴리스하지 않은 변경은
`Unreleased` 에 쌓는다. 번호 규칙과 쓰는 법은 [references/release.md](references/release.md) 에 있다.

## Unreleased

## 0.2.0 — 2026-10-15

### 추가

- `search flights --brief` — 결과 모양은 그대로 두고 구간별 공항·시각·기종, 항공권 `url` 처럼 고르는 데 필요 없는 키를 뺀 요약 (두 소스) (#35)
- `flight --offer-urls N` — 판매처 예약 링크를 앞의 운임 N개에만 붙인다. 뒤 운임은 `url` 키가 없고, `0` 이면 링크 없이 가격만 (두 소스) (#36)

### 변경

- 파이프로 받는 `--help` 를 상자 그림 없는 평문으로 쓴다. 같은 도움말이 절반 아래로 짧고, 좁은 칸에서 설명이 `…` 로 잘리지 않는다 (터미널은 그대로) (#34)

### 수정

- 네이버 국제선 `aircraft` 가 기종을 모를 때 빈 문자열(`""`) 대신 `null` 이다 (#33)

## 0.1.0 — 2026-10-14

### 추가

- 첫 릴리스: 네이버 항공권·스카이스캐너에서 지금 살 수 있는 항공권·날짜별 최저가·목적지를 조회하는 CLI 와 `baenggipyo guide`
- `baenggipyo --version` — 설치한 버전과 커밋을 출력
- PyPI 에서 설치할 수 있다: `uv tool install baenggipyo`, `pipx install baenggipyo`, `pip install baenggipyo`, 설치 없이 `uvx baenggipyo`. `baenggipyo==0.1.0` 처럼 버전도 고를 수 있다 (#3)
