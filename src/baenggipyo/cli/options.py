"""여러 명령이 똑같이 쓰는 옵션 별칭 (두 소스 CLI 와 최상위 `limits`). 도움말까지 같은 것만 둔다 (다르면 각 소스 CLI 에 둔다)."""

import typer

from typing import Annotated


PrettyOption = Annotated[
    bool | None,
    typer.Option('--pretty/--compact', help='JSON 들여쓰기. 기본값은 터미널이면 pretty, 파이프면 compact'),
]
AdultsOption = Annotated[int, typer.Option('--adults', min=1, max=9, help='성인 수 (만 12세 이상)')]
ChildrenOption = Annotated[int, typer.Option('--children', min=0, max=8, help='소아 수 (만 2~11세)')]
InfantsOption = Annotated[int, typer.Option('--infants', min=0, max=8, help='유아 수 (만 2세 미만, 성인 수 이하)')]
CabinOption = Annotated[str, typer.Option('--cabin', help='좌석 등급: 일반석, 프리미엄일반석, 비즈니스석, 일등석')]
DirectOption = Annotated[bool, typer.Option('--direct', help='직항만')]
MonthOption = Annotated[str | None, typer.Option('--month', help='출발 달 YYYY-MM (기본: 이번 달)')]
TripDaysOption = Annotated[
    int | None,
    typer.Option('--trip-days', min=1, max=60, help='왕복 여행 일수. 가는 날과 오는 날을 모두 센다 (2박 3일 = 3). 주지 않으면 편도'),
]
OriginArgument = Annotated[str | None, typer.Argument(help='출발 IATA 공항·도시 코드 또는 이름 (예: ICN, SEL, 인천)')]
DestinationArgument = Annotated[str | None, typer.Argument(help='도착 IATA 공항·도시 코드 또는 이름 (예: NRT, TYO, 도쿄)')]
DepartOption = Annotated[str | None, typer.Option('--depart', help='가는 날 YYYY-MM-DD')]
ReturnOption = Annotated[str | None, typer.Option('--return', help='오는 날 YYYY-MM-DD. 주면 왕복')]
LegOption = Annotated[
    list[str] | None,
    typer.Option('--leg', help='다구간 구간 출발-도착-YYYY-MM-DD. 구간마다 반복 (예: --leg ICN-NRT-2026-11-01 --leg KIX-ICN-2026-11-05)'),
]
AirlineOption = Annotated[list[str] | None, typer.Option('--airline', help='항공사 IATA 코드만 (예: KE,OZ). 반복하거나 쉼표로 구분')]
SortOption = Annotated[str, typer.Option('--sort', help='정렬: 가격, 출발시각, 소요시간')]
AirportQueryArgument = Annotated[str, typer.Argument(help='공항·도시 이름 또는 코드 (예: 도쿄, 나리타, OSA)')]
AirportLimitOption = Annotated[int, typer.Option('--limit', min=1, max=20, help='후보 수')]
FlightIdArgument = Annotated[str, typer.Argument(metavar='ID', help='search flights 결과의 id')]
BriefOption = Annotated[
    bool,
    typer.Option('--brief', help='요약: 항공권마다 고르는 데 필요한 값만 (구간별 공항·시각·기종, 항공권 url 등을 뺀다. 결과 모양은 같다)'),
]
