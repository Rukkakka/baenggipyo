"""`baenggipyo naver` 하위 명령. 사용법은 docs/naver.md, 구현 메모는 references/providers/naver.md."""

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
    InfantsOption,
    LegOption,
    MonthOption,
    OfferUrlsOption,
    OriginArgument,
    PrettyOption,
    ReturnOption,
    SortOption,
    TripDaysOption,
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
from baenggipyo.providers.naver.api.client import NaverClient
from baenggipyo.providers.naver.service.airport import (
    resolve_legs,
    resolve_locations,
    route_kind,
    search_airports,
)
from baenggipyo.providers.naver.service.calendar import get_calendar
from baenggipyo.providers.naver.service.common import (
    DEFAULT_LIMIT,
    DEFAULT_OFFER_LIMIT,
    FLIGHT_SORTS,
    MAX_LIMIT,
    MAX_OFFER_LIMIT,
    NaverFlightConditions,
)
from baenggipyo.providers.naver.service.domestic import (
    get_domestic_flights,
    is_domestic_flight_id,
    search_domestic_flights,
)
from baenggipyo.providers.naver.service.explore import (
    EXPLORE_DEFAULT_LIMIT,
    EXPLORE_MAX_LIMIT,
    explore,
)
from baenggipyo.providers.naver.service.flight import (
    get_flight,
    search_flights,
)

from typing import Annotated


SOURCE: str = 'naver'


app = typer.Typer(
    help='네이버 항공권 (flight.naver.com) 국제선·국내선 조회. 금액은 원, 시각은 각 공항 현지 시각.',
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
    """공항·도시 IATA 코드 찾기 (요청 1회). 도시 코드(TYO)는 그 도시의 모든 공항을 찾는다.

    예: baenggipyo naver search airports 오사카
    """
    with handle_errors(SOURCE), NaverClient() as client:
        emit(search_airports(client, query, limit=limit), pretty)


def _conditions(
    adults: int,
    children: int,
    infants: int,
    cabin: str,
    direct: bool,
    airline: list[str] | None,
) -> NaverFlightConditions:
    validate_passengers(adults, children, infants)
    return NaverFlightConditions(
        adults=adults,
        children=children,
        infants=infants,
        cabin=one_of(cabin, CABIN_CLASSES, '--cabin', CABIN_ALIASES),
        direct=direct,
        airlines=parse_airlines(airline),
    )


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
    """편도·왕복·다구간 항공권 (요청: 공항 해석 입력마다 1회 + 판매처 이름 1회 + 검색 1~2회, 국제선 10~20초·국내선 수 초).

    대표 가격(price_per_adult_krw, total_price_krw)은 어떤 결제수단으로도 받을 수 있는 운임의 최저가다. 특정 카드로만 받는 더
    싼 가격은 card_discount_* 로 따로 준다. 국내선 왕복(김포↔제주 등)은 결과 모양이 다르다: itineraries 대신 가는 편·오는 편
    목록(legs[])에서 하나씩 고른다. 국내선 다구간과 국내·국제가 섞인 노선은 찾지 않는다.

    예: baenggipyo naver search flights ICN NRT --depart 2026-11-15 --return 2026-11-20
    """
    raw_legs = parse_legs(origin, destination, depart, return_, leg)
    sort_by = one_of(sort, FLIGHT_SORTS, '--sort', SORT_ALIASES)
    with handle_errors(SOURCE), NaverClient() as client:
        conditions = _conditions(adults, children, infants, cabin, direct, airline)
        legs = resolve_legs(client, raw_legs)
        if route_kind(legs) == 'domestic':
            domestic = search_domestic_flights(client, legs, conditions=conditions, sort=sort_by, limit=limit)
            emit(domestic, pretty, notes=domestic.notes, brief=brief)
            return
        result = search_flights(client, legs, conditions=conditions, sort=sort_by, limit=limit)
        emit(result, pretty, notes=result.notes, brief=brief)


@app.command('flight')
def flight_command(
    flight_ids: Annotated[
        list[str],
        typer.Argument(metavar='ID...', help='search flights 결과의 id. 국내선 왕복은 가는 편·오는 편 id 를 함께 준다')
    ],
    limit: Annotated[
        int,
        typer.Option('--limit', min=1, max=MAX_OFFER_LIMIT, help='판매처 운임 수 (모든 결제수단 운임이 먼저 온다)')
    ] = DEFAULT_OFFER_LIMIT,
    offer_urls: OfferUrlsOption = None,
    pretty: PrettyOption = None,
) -> None:
    """항공권의 판매처별 가격과 예약 링크 (요청 2~3회, 검색 뒤 15분이 지났으면 같은 조건으로 다시 검색한다).

    국제선은 id 하나, 국내선은 편 하나(편도) 또는 가는 편·오는 편 둘(왕복 예약 가격)을 받는다.

    예: baenggipyo naver flight Y-100-0-ICNaNRTa20261115~20261115ICNNRTZE0605
    """
    domestic = [is_domestic_flight_id(value) for value in flight_ids]
    if any(domestic) and not all(domestic):
        raise typer.BadParameter('국내선 id 와 국제선 id 를 함께 줄 수 없습니다', param_hint='ID')
    if not any(domestic) and len(flight_ids) != 1:
        raise typer.BadParameter('국제선은 id 를 하나만 줍니다', param_hint='ID')
    if len(flight_ids) > 2:
        raise typer.BadParameter('국내선은 가는 편·오는 편 id 를 두 개까지 줍니다', param_hint='ID')
    with handle_errors(SOURCE), NaverClient() as client:
        if all(domestic):
            booking, booking_notes = get_domestic_flights(client, flight_ids, offer_limit=limit, url_limit=offer_urls)
            emit(booking, pretty, notes=booking_notes)
            return
        detail, notes = get_flight(client, flight_ids[0], offer_limit=limit, url_limit=offer_urls)
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
    trip_days: TripDaysOption = None,
    direct: DirectOption = False,
    pretty: PrettyOption = None,
) -> None:
    """한 달의 날짜별 최저가 (요청 3회: 공항 해석 2회 + 달력 1회). 실시간이 아닌 추정가다.

    --trip-days 를 주면 출발일마다 그 일수의 왕복 최저가다.

    예: baenggipyo naver calendar ICN NRT --month 2026-11 --trip-days 4
    """
    month_value = parse_month(month, '--month')
    with handle_errors(SOURCE), NaverClient() as client:
        origin_location, destination_location = resolve_locations(client, origin, destination)
        result = get_calendar(client, origin_location, destination_location, month=month_value, trip_days=trip_days, direct=direct)
        emit(result, pretty, notes=result.notes)


@app.command('explore')
def explore_command(
    origin: Annotated[
        str,
        typer.Argument(help='출발 IATA 공항·도시 코드 또는 이름 (예: SEL, ICN)')
    ],
    month: Annotated[
        str | None,
        typer.Option('--month', help='출발 달 YYYY-MM (주지 않으면 네이버 기본 범위)')
    ] = None,
    trip_days: TripDaysOption = None,
    direct: DirectOption = False,
    limit: Annotated[
        int,
        typer.Option('--limit', min=1, max=EXPLORE_MAX_LIMIT, help='목적지 수')
    ] = EXPLORE_DEFAULT_LIMIT,
    pretty: PrettyOption = None,
) -> None:
    """어디든지: 국제선 목적지별 최저가를 싼 순으로 (요청 2회: 공항 해석 + 조회). 실시간이 아닌 추정가다.

    예: baenggipyo naver explore SEL --month 2026-11 --trip-days 4
    """
    month_value = parse_month(month, '--month') if month is not None else None
    with handle_errors(SOURCE), NaverClient() as client:
        [origin_location] = resolve_locations(client, origin)
        result = explore(client, origin_location.code, month=month_value, trip_days=trip_days, direct=direct, limit=limit)
        emit(result, pretty, notes=result.notes)
