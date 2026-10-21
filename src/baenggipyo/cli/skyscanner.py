"""`baenggipyo skyscanner` 하위 명령. 사용법은 docs/skyscanner.md, 구현 메모는 references/providers/skyscanner.md."""

import typer

from baenggipyo.cli.options import (
    CABIN_ALIASES,
    SORT_ALIASES,
    AdultsOption,
    AirlineOption,
    AirportLimitOption,
    AirportQueryArgument,
    BriefOption,
    CabinOption,
    ChildrenOption,
    DepartOption,
    DestinationArgument,
    DirectOption,
    FlightIdArgument,
    InfantsOption,
    LegOption,
    MonthOption,
    OfferUrlsOption,
    OriginArgument,
    PrettyOption,
    ReturnOption,
    SortOption,
)
from baenggipyo.cli.output import (
    emit,
    handle_errors,
    one_of,
    parse_airlines,
    parse_legs,
    parse_month,
    validate_passengers,
)
from baenggipyo.core.flight import CABIN_CLASSES
from baenggipyo.providers.skyscanner.api.client import SkyscannerClient
from baenggipyo.providers.skyscanner.service.airport import (
    resolve_legs,
    resolve_places,
    search_airports,
)
from baenggipyo.providers.skyscanner.service.calendar import get_calendar
from baenggipyo.providers.skyscanner.service.explore import (
    EXPLORE_DEFAULT_LIMIT,
    EXPLORE_MAX_LIMIT,
    explore,
)
from baenggipyo.providers.skyscanner.service.flight import (
    DEFAULT_LIMIT,
    DEFAULT_OFFER_LIMIT,
    FLIGHT_SORTS,
    MAX_LIMIT,
    MAX_OFFER_LIMIT,
    SkyscannerFlightConditions,
    get_flight,
    search_flights,
)

from typing import Annotated


SOURCE: str = 'skyscanner'

app = typer.Typer(
    help='스카이스캐너 (skyscanner.co.kr) 항공권 조회 (국내선 포함). 금액은 원, 시각은 각 공항 현지 시각.',
    no_args_is_help=True,
)
search_app = typer.Typer(help='공항·도시 코드 또는 항공권을 찾는다', no_args_is_help=True)
app.add_typer(search_app, name='search')


@search_app.command('airports')
def search_airports_command(
    query: AirportQueryArgument,
    limit: AirportLimitOption = 10,
    pretty: PrettyOption = None,
) -> None:
    """공항·도시 IATA 코드 찾기 (요청 1회).

    예: baenggipyo skyscanner search airports 도쿄
    """
    with handle_errors(SOURCE), SkyscannerClient() as client:
        emit(search_airports(client, query, limit=limit), pretty)


@search_app.command('flights')
def search_flights_command(
    origin: OriginArgument = None,
    destination: DestinationArgument = None,
    depart: DepartOption = None,
    return_: ReturnOption = None,
    leg: LegOption = None,
    adults: AdultsOption = 1,
    children: ChildrenOption = 0,
    infants: InfantsOption = 0,
    cabin: CabinOption = '일반석',
    direct: DirectOption = False,
    airline: AirlineOption = None,
    sort: SortOption = '가격',
    limit: Annotated[
        int,
        typer.Option('--limit', min=1, max=MAX_LIMIT, help='돌려줄 항공권 수')
    ] = DEFAULT_LIMIT,
    brief: BriefOption = False,
    pretty: PrettyOption = None,
) -> None:
    """편도·왕복·다구간 항공권, 국내선 포함 (요청: 공항 해석 입력마다 1회 + 검색 1회 + 폴링, 보통 5~15초).

    가격은 모든 승객 합계다 (성인 1명이면 1인 요금과 같다). 직항·항공사 조건과 정렬은 받은 결과에서 baenggipyo 가 한다.

    예: baenggipyo skyscanner search flights GMP CJU --depart 2026-11-15 --return 2026-11-17
    """
    raw_legs = parse_legs(origin, destination, depart, return_, leg)
    validate_passengers(adults, children, infants)
    sort_by = one_of(sort, FLIGHT_SORTS, '--sort', SORT_ALIASES)
    with handle_errors(SOURCE), SkyscannerClient() as client:
        conditions = SkyscannerFlightConditions(
            adults=adults,
            children=children,
            infants=infants,
            cabin=one_of(cabin, CABIN_CLASSES, '--cabin', CABIN_ALIASES),
            direct=direct,
            airlines=parse_airlines(airline),
        )
        legs = resolve_legs(client, raw_legs)
        result = search_flights(client, legs, conditions=conditions, sort=sort_by, limit=limit)
        emit(result, pretty, notes=result.notes, brief=brief)


@app.command('flight')
def flight_command(
    flight_id: FlightIdArgument,
    limit: Annotated[
        int,
        typer.Option('--limit', min=1, max=MAX_OFFER_LIMIT, help='판매처 운임 수 (싼 순. 판매처 여러 곳의 조합은 줄이 여럿이어도 운임 하나로 센다)')
    ] = DEFAULT_OFFER_LIMIT,
    offer_urls: OfferUrlsOption = None,
    pretty: PrettyOption = None,
) -> None:
    """항공권 하나의 판매처별 가격과 예약 링크. 같은 조건으로 다시 검색해 찾는다 (검색 1회 + 폴링).

    예: baenggipyo skyscanner flight <search flights 결과의 id>
    """
    with handle_errors(SOURCE), SkyscannerClient() as client:
        detail, notes = get_flight(client, flight_id, offer_limit=limit, url_limit=offer_urls)
        emit(detail, pretty, notes=notes)


@app.command('calendar')
def calendar_command(
    origin: Annotated[
        str,
        typer.Argument(help='출발 IATA 공항·도시 코드 또는 이름')
    ],
    destination: Annotated[
        str,
        typer.Argument(help='도착 IATA 공항·도시 코드 또는 이름')
    ],
    month: MonthOption = None,
    pretty: PrettyOption = None,
) -> None:
    """한 달의 편도 날짜별 최저가 (요청 3회: 공항 해석 2회 + 달력 1회). 실시간이 아닌 추정가이고 편도만 된다.

    예: baenggipyo skyscanner calendar ICN NRT --month 2026-11
    """
    month_value = parse_month(month, '--month')
    with handle_errors(SOURCE), SkyscannerClient() as client:
        origin_place, destination_place = resolve_places(client, origin, destination)
        result = get_calendar(client, origin_place, destination_place, month=month_value)
        emit(result, pretty, notes=result.notes)


@app.command('explore')
def explore_command(
    origin: Annotated[
        str,
        typer.Argument(help='출발 IATA 공항·도시 코드 또는 이름 (예: SEL, ICN)')
    ],
    month: Annotated[
        str | None,
        typer.Option('--month', help='출발 달 YYYY-MM (주지 않으면 언제든)')
    ] = None,
    round_trip: Annotated[
        bool,
        typer.Option('--round-trip', help='같은 달 안의 왕복 최저가 (여행 일수는 정할 수 없다)')
    ] = False,
    direct: DirectOption = False,
    limit: Annotated[
        int,
        typer.Option('--limit', min=1, max=EXPLORE_MAX_LIMIT, help='도시 수')
    ] = EXPLORE_DEFAULT_LIMIT,
    pretty: PrettyOption = None,
) -> None:
    """어디든지: 도시별 최저가를 싼 순으로, 국내 포함 (요청 2회: 공항 해석 + 조회). 지표 가격(추정가)이고 날짜는 주지 않는다.

    예: baenggipyo skyscanner explore SEL --month 2026-11 --round-trip
    """
    month_value = parse_month(month, '--month') if month is not None else None
    with handle_errors(SOURCE), SkyscannerClient() as client:
        [origin_place] = resolve_places(client, origin)
        result = explore(client, origin_place, month=month_value, round_trip=round_trip, direct=direct, limit=limit)
        emit(result, pretty, notes=result.notes)
