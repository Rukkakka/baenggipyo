"""국내선 항공권 검색과 판매처별 가격 (`search flights`, `flight` 의 국내선).

국내선은 국제선과 API·화면 구조가 다르다. 왕복이어도 가는 편과 오는 편을 따로 고르고(목록 두 개), 운임도 편마다 따로다.
그래서 결과는 방향(`legs[]`)마다 편 목록이고, `flight` 는 고른 편 하나(편도) 또는 둘(가는 편 + 오는 편)을 받는다.
"""

import re

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from baenggipyo.core.errors import (
    BaenggipyoError,
    fare_missing_error,
)
from baenggipyo.core.flight import (
    CabinClass,
    Journey,
    SearchLeg,
    SearchSummary,
    Segment,
    TripType,
    compact_datetime,
    shown_offers,
)
from baenggipyo.core.schema import built_from_response
from baenggipyo.providers.naver.api.client import NaverClient
from baenggipyo.providers.naver.api.request.common import (
    NaverBookingItineraryModel,
    NaverBookingPersonModel,
    NaverFlightFilterModel,
)
from baenggipyo.providers.naver.api.request.domestic import (
    NaverBookingOptionsDomesticRequestPayloadModel,
    NaverDomesticBookingParamsModel,
    NaverDomesticBookingSearchParamsModel,
    NaverDomesticItineraryModel,
    NaverDomesticOtaFareModel,
    NaverFlightDomesticSearchFlightsRequestPayloadModel,
)
from baenggipyo.providers.naver.api.response.common import NaverOtaModel
from baenggipyo.providers.naver.api.response.domestic import (
    NaverDomesticFareModel,
    NaverDomesticFlightModel,
    NaverDomesticStatusModel,
)
from baenggipyo.providers.naver.service.common import (
    CACHE_LATEST_VERSION,
    DEFAULT_LIMIT,
    DEFAULT_OFFER_LIMIT,
    MAX_LIMIT,
    MAX_OFFER_LIMIT,
    SITE_URL,
    NaverFlightConditions,
    NaverFlightSort,
    NaverItinerary,
    NaverLeg,
    NaverLocation,
    NaverOffer,
    cut_short,
    flight_number_of,
    incomplete_note,
    partner_names,
    seller_type_of,
    trip_type_of,
    unwrap_graphql,
)

from typing import (
    Annotated,
    Literal,
)


DomesticDirection = Literal['departure', 'arrival']
DirectionLabel = Literal['가는 편', '오는 편']

# 번들의 좌석 등급 이름표 (NON_REFUNDABLE·NO_FREE_BAGGAGE·SPECIAL_DEAL·DISCOUNT·ECONOMY·BUSINESS)
SEAT_CLASS_LABELS: dict[str, str] = {
    'NR': '특가석-환불불가',
    'NB': '특가석-수하물유료',
    'L': '특가석',
    'D': '할인석',
    'Y': '일반석',
    'C': '비즈니스석',
}
# 국내선에는 프리미엄일반석·일등석이 없다. 서버 캐시 키는 늘 YC(전체)라 좌석은 filter.seatClass 로 건다 (2026-10-11 실측)
SEAT_FILTER_BY_CABIN: dict[CabinClass, list[str]] = {
    '일반석': ['NR', 'NB', 'L', 'D', 'Y'],
    '비즈니스석': ['C'],
}
CABIN_LETTER_BY_LABEL: dict[CabinClass, str] = {
    '일반석': 'Y',
    '비즈니스석': 'C',
}
CABIN_BY_LETTER: dict[str, CabinClass] = {letter: label for label, letter in CABIN_LETTER_BY_LABEL.items()}
DOMESTIC_SORT_BY_LABEL: dict[NaverFlightSort, dict[str, int]] = {
    '가격': {'minFare': 1},
    '출발시각': {'segment.departure.time': 1, 'minFare': 1},
    '소요시간': {'duration': 1, 'minFare': 1},
}
DIRECTION_LABELS: dict[DomesticDirection, DirectionLabel] = {
    'departure': '가는 편',
    'arrival': '오는 편',
}
CARD_DISCOUNT_TYPE_LABELS: dict[str, str] = {
    'C': '카드 할인',
}
# 판매처 조회에서 편 하나를 찾을 때 받을 편 수 (같은 항공사·좌석 등급 안에서 찾는다)
DETAIL_FETCH_LIMIT: int = 100

# 국제선 ID 의 구간(service/flight.py 의 LEG_ID_PATTERN, 공항·도시 구분 글자 포함)과 모양이 달라 DOMESTIC_ 을 붙여 구분한다
DOMESTIC_LEG_ID_PATTERN: str = r'[A-Z]{3}[A-Z]{3}\d{8}'
DOMESTIC_FLIGHT_ID_PATTERN: re.Pattern[str] = re.compile(
    r'^D-(?P<adults>\d)(?P<children>\d)(?P<infants>\d)-(?P<cabin>[YC])-'
    rf'(?P<legs>{DOMESTIC_LEG_ID_PATTERN}(?:_{DOMESTIC_LEG_ID_PATTERN})?)~(?P<direction>departure|arrival)\.(?P<itinerary_id>[0-9A-Z]+)\.(?P<seat_class>[A-Z]{{1,2}})$'
)
DOMESTIC_LEG_ID_PARTS: re.Pattern[str] = re.compile(r'([A-Z]{3})([A-Z]{3})(\d{4})(\d{2})(\d{2})')
ITINERARY_AIRLINE_PATTERN: re.Pattern[str] = re.compile(r'^\d{12}[A-Z]{3}[A-Z]{3}([A-Z0-9]{2})')


class NaverDomesticItinerary(NaverItinerary):
    """국내선 편 하나. 좌석 등급과 남은 좌석을 더한다."""

    direction: Annotated[
        DirectionLabel,
        Field(description='가는 편 또는 오는 편')
    ]
    seat_class: Annotated[
        str | None,
        Field(description='좌석 등급 (특가석·특가석-환불불가·특가석-수하물유료·할인석·일반석·비즈니스석)')
    ] = None
    seats_left: Annotated[
        int | None,
        Field(description='남은 좌석')
    ] = None

    def brief_omitted_fields(self) -> frozenset[str]:
        # 방향은 legs[].direction 에 있다
        return super().brief_omitted_fields() | {'direction'}


class NaverDomesticLegResult(BaseModel):
    """방향 하나(가는 편 또는 오는 편)의 편 목록."""

    model_config = ConfigDict(
        extra='forbid',
    )

    direction: Annotated[
        DirectionLabel,
        Field(description='가는 편 또는 오는 편')
    ]
    origin: Annotated[
        str,
        Field(description='출발 공항')
    ]
    destination: Annotated[
        str,
        Field(description='도착 공항')
    ]
    date: Annotated[
        str,
        Field(description='출발 날짜')
    ]
    returned: Annotated[
        int,
        Field(description='돌려준 편 수')
    ]
    itineraries: Annotated[
        list[NaverDomesticItinerary],
        Field(description='편 (정렬 순서대로)')
    ]


class NaverDomesticSearchResult(BaseModel):
    """국내선 `search flights` 결과."""

    model_config = ConfigDict(
        extra='forbid',
    )

    search: Annotated[
        SearchSummary,
        Field(description='검색 조건')
    ]
    legs: Annotated[
        list[NaverDomesticLegResult],
        Field(description='방향마다 편 목록 (왕복은 가는 편·오는 편에서 하나씩 고른다)')
    ]
    notes: Annotated[
        list[str],
        Field(exclude=True, description='결과 안내')
    ] = []


class NaverDomesticBooking(BaseModel):
    """국내선 `flight` 결과: 고른 편과 판매처별 가격."""

    model_config = ConfigDict(
        extra='forbid',
    )

    source: Annotated[
        Literal['naver'],
        Field(description='소스')
    ] = 'naver'
    trip_type: Annotated[
        TripType,
        Field(description='편도 또는 왕복')
    ]
    url: Annotated[
        str,
        Field(description='원 사이트 검색 결과 페이지')
    ]
    flights: Annotated[
        list[NaverDomesticItinerary],
        Field(description='고른 편 (방금 다시 조회한 값)')
    ]
    total_price_krw: Annotated[
        int | None,
        Field(description='고른 편 전부의 대표 가격 합계 (모든 승객)')
    ] = None
    offer_count: Annotated[
        int,
        Field(description='판매처 운임 수 (따로 예약하는 조합의 두 줄은 하나로 센다)')
    ]
    offers: Annotated[
        list[NaverOffer],
        Field(description='판매처별 가격 (싼 순). 판매처가 다른 조합은 조합 합계로 함께 세운다')
    ]


def fare_per_person(fare: NaverDomesticFareModel) -> tuple[int, int]:
    """(성인, 소아) 1인 총액. 사이트 계산식: 운임 + 유류할증료 + 세금 + 발권 수수료."""
    fee = fare.publish_fee or 0
    return fare.adult_fare + fare.a_fuel + fare.a_tax + fee, fare.child_fare + fare.c_fuel + fare.c_tax + fee


def fare_total(fare: NaverDomesticFareModel, conditions: NaverFlightConditions) -> int | None:
    """모든 승객 합계. 사이트도 유아 운임은 계산에 넣지 않아, 유아가 있으면 합계를 모른다(None)."""
    if conditions.infants:
        return None
    adult, child = fare_per_person(fare)
    if conditions.children and fare.child_fare == 0:
        # 소아 운임을 주지 않는 판매처다 (실측: 운임 0, 세금 0)
        return None
    return adult * conditions.adults + child * conditions.children


def _airport(location: NaverLocation) -> str:
    """국내선 API 는 공항 코드만 받는다. 공항이 하나뿐인 도시(CJU, PUS)는 그 공항으로 바꾼다."""
    if location.type == 'airport':
        return location.code
    if location.airports and len(location.airports) == 1:
        return location.airports[0]
    raise BaenggipyoError(
        code='INVALID_ARGUMENT',
        message=f"국내선은 공항 코드로 찾습니다. 공항이 여럿인 도시입니다: {location.code} ({', '.join(location.airports or [])})",
        hint='공항 코드로 다시 주세요 (예: 서울은 GMP, 부산은 PUS)',
    )


def _legs_with_airports(legs: list[NaverLeg]) -> list[NaverLeg]:
    return [
        NaverLeg(
            origin=NaverLocation(code=_airport(leg.origin), type='airport', name=leg.origin.name, domestic=True),
            destination=NaverLocation(code=_airport(leg.destination), type='airport', name=leg.destination.name, domestic=True),
            date=leg.date,
        )
        for leg in legs
    ]


def domestic_results_url(legs: list[NaverLeg], conditions: NaverFlightConditions) -> str:
    """국내선 검색 결과 페이지 (`/flights/domestic/GMP-CJU-20261115/CJU-GMP-20261117?adult=1&fareType=YC`, 2026-10-11 실측).

    국제선 결과 페이지(`common.results_url`)와 경로·쿼리가 달라 따로 둔다.
    """
    routes = '/'.join(f'{leg.origin.code}-{leg.destination.code}-{leg.compact_date}' for leg in legs)
    query = f'adult={conditions.adults}&fareType=YC'
    if conditions.children:
        query += f'&child={conditions.children}'
    if conditions.infants:
        query += f'&infant={conditions.infants}'
    return f'{SITE_URL}/flights/domestic/{routes}?{query}'


def domestic_flight_id(legs: list[NaverLeg], conditions: NaverFlightConditions, flight: NaverDomesticFlightModel, direction: DomesticDirection) -> str:
    """`flight` 에 넘길 국내선 편 ID. 서버 캐시 키가 검색 조건이라 조건을 함께 담는다.

    형식: `D-<성인><소아><유아>-<Y|C>-<출발><도착><YYYYMMDD>[_…]~<departure|arrival>.<편 ID>.<좌석 등급>`.
    예: `D-100-Y-GMPCJU20261115_CJUGMP20261117~departure.202611151910GMPCJUWE6503.L`.
    """
    leg_ids = '_'.join(f'{leg.origin.code}{leg.destination.code}{leg.compact_date}' for leg in legs)
    cabin = CABIN_LETTER_BY_LABEL[conditions.cabin]
    return (
        f'D-{conditions.adults}{conditions.children}{conditions.infants}-{cabin}-{leg_ids}'
        f'~{direction}.{flight.itinerary_id}.{flight.seat_class}'
    )


def is_domestic_flight_id(value: str) -> bool:
    return value.strip().startswith('D-')


def parse_domestic_flight_id(value: str) -> tuple[list[NaverLeg], NaverFlightConditions, DomesticDirection, str, str]:
    """`domestic_flight_id` 를 되돌린다.

    Raises:
        BaenggipyoError: 형식이 다를 때 (`INVALID_ARGUMENT`).
    """
    match = DOMESTIC_FLIGHT_ID_PATTERN.fullmatch(value.strip())
    if match is None:
        raise BaenggipyoError(
            code='INVALID_ARGUMENT',
            message=f'네이버 국내선 편 ID 형식이 아닙니다: {value}',
            hint='naver search flights 결과의 id 를 그대로 넣으세요 (예: D-100-Y-GMPCJU20261115~departure.202611151910GMPCJUWE6503.L)',
        )
    legs = [
        NaverLeg(
            origin=NaverLocation(code=origin, type='airport', domestic=True),
            destination=NaverLocation(code=destination, type='airport', domestic=True),
            date=f'{year}-{month}-{day}',
        )
        for origin, destination, year, month, day in DOMESTIC_LEG_ID_PARTS.findall(match['legs'])
    ]
    conditions = NaverFlightConditions(
        adults=int(match['adults']),
        children=int(match['children']),
        infants=int(match['infants']),
        cabin=CABIN_BY_LETTER[match['cabin']],
    )
    direction: DomesticDirection = 'departure' if match['direction'] == 'departure' else 'arrival'
    return legs, conditions, direction, match['itinerary_id'], match['seat_class']


def _search_payload(
    legs: list[NaverLeg],
    conditions: NaverFlightConditions,
    *,
    direction: DomesticDirection,
    sort: NaverFlightSort,
    limit: int,
    initial: bool,
    version: int | None = None,
    seat_classes: list[str] | None = None,
    airlines: list[str] | None = None,
) -> NaverFlightDomesticSearchFlightsRequestPayloadModel:
    flight_filter: dict[str, object] = {'type': direction, 'seatClass': seat_classes or SEAT_FILTER_BY_CABIN[conditions.cabin]}
    if airlines:
        flight_filter['segment__airlineCode'] = airlines
    return NaverFlightDomesticSearchFlightsRequestPayloadModel(
        itineraries=[
            NaverDomesticItineraryModel(departure_airport=leg.origin.code, arrival_airport=leg.destination.code, departure_date=leg.compact_date)
            for leg in legs
        ],
        person=NaverBookingPersonModel(adult=conditions.adults, child=conditions.children, infant=conditions.infants),
        trip_type='RT' if len(legs) == 2 else 'OW',
        initial_request=initial,
        version=version,
        flight_filter=NaverFlightFilterModel(filter=flight_filter, limit=limit, sort=DOMESTIC_SORT_BY_LABEL[sort]),
    )


def _cheapest(flight: NaverDomesticFlightModel, partners: dict[str, str]) -> NaverDomesticFareModel | None:
    """대표 운임: 성인 1인 총액 최저. 같은 값이면 이름을 아는 판매처를 고른다."""
    return min(flight.fares, key=lambda fare: (fare_per_person(fare)[0], fare.agt_code not in partners), default=None)


def _card_discount(flight: NaverDomesticFlightModel, general: NaverDomesticFareModel | None) -> NaverDomesticFareModel | None:
    """대표 운임보다 싼 카드 할인 운임 중 최저가."""
    ceiling = fare_per_person(general)[0] if general else None
    candidates = [
        fare for fare in flight.fares
        if fare.discount_fare is not None and fare.discount_fare.adult_discount_fare is not None
        and (ceiling is None or fare.discount_fare.adult_discount_fare < ceiling)
    ]
    return min(candidates, key=lambda fare: fare.discount_fare.adult_discount_fare or 0 if fare.discount_fare else 0, default=None)


def _itinerary(
    flight: NaverDomesticFlightModel,
    status: NaverDomesticStatusModel,
    partners: dict[str, str],
    *,
    legs: list[NaverLeg],
    conditions: NaverFlightConditions,
    direction: DomesticDirection,
) -> NaverDomesticItinerary:
    segment = flight.segment
    op_code = segment.op_code or None
    journey = Journey(
        departure_airport=segment.departure.airport_code,
        arrival_airport=segment.arrival.airport_code,
        departure_at=compact_datetime(segment.departure.local_date_time),
        arrival_at=compact_datetime(segment.arrival.local_date_time),
        duration_minutes=flight.duration,
        stops=0,
        stopover_airports=[],
        segments=[Segment(
            airline_code=segment.airline_code,
            airline=status.airlines_code_map.get(segment.airline_code),
            flight_number=flight_number_of(segment.airline_code, segment.flight_number),
            operating_airline_code=op_code,
            operating_airline=status.op_code_map.get(op_code) if op_code else None,
            departure_airport=segment.departure.airport_code,
            arrival_airport=segment.arrival.airport_code,
            departure_at=compact_datetime(segment.departure.local_date_time),
            arrival_at=compact_datetime(segment.arrival.local_date_time),
            duration_minutes=segment.flight_duration,
        )],
    )
    general = _cheapest(flight, partners)
    discount = _card_discount(flight, general)
    discount_info = discount.discount_fare.discount_info if discount and discount.discount_fare else None
    return NaverDomesticItinerary(
        source='naver',
        id=domestic_flight_id(legs, conditions, flight, direction),
        url=domestic_results_url(legs, conditions),
        trip_type='편도',
        cabin='비즈니스석' if flight.seat_class == 'C' else '일반석',
        journeys=[journey],
        total_price_krw=fare_total(general, conditions) if general else None,
        price_per_adult_krw=fare_per_person(general)[0] if general else None,
        offer_count=len({fare.agt_code for fare in flight.fares}),
        cheapest_seller=partners.get(general.agt_code) if general else None,
        # 수하물 유료 특가석만 알 수 있다. 나머지는 운임 정보에 수하물이 없다
        baggage_included=False if flight.seat_class == 'NB' else None,
        card_discount_price_krw=discount.discount_fare.adult_discount_fare if discount and discount.discount_fare else None,
        card_discount_name=discount_info.card_type.name if discount_info and discount_info.card_type else None,
        card_discount_category=CARD_DISCOUNT_TYPE_LABELS.get(discount_info.discount_type or '') if discount_info else None,
        direction=DIRECTION_LABELS[direction],
        seat_class=SEAT_CLASS_LABELS.get(flight.seat_class, flight.seat_class),
        seats_left=flight.seat_count,
    )


def _search_note(status: NaverDomesticStatusModel | None, *, missing: str = '더 싼 항공권') -> list[str]:
    if status is None:
        return []
    return incomplete_note(status.is_complete, status.complete_partner_count, status.available_partner_count, missing=missing)


def _summary(legs: list[NaverLeg], conditions: NaverFlightConditions) -> SearchSummary:
    return SearchSummary(
        trip_type=trip_type_of(legs),
        legs=[SearchLeg(origin=leg.origin.code, destination=leg.destination.code, date=leg.date) for leg in legs],
        cabin=conditions.cabin,
        adults=conditions.adults,
        children=conditions.children,
        infants=conditions.infants,
        direct=True,
        url=domestic_results_url(legs, conditions),
    )


def check_domestic_conditions(conditions: NaverFlightConditions) -> None:
    """국내선에 없는 좌석 등급을 요청 전에 막는다.

    Raises:
        BaenggipyoError: 프리미엄일반석·일등석일 때 (`INVALID_ARGUMENT`).
    """
    if conditions.cabin not in SEAT_FILTER_BY_CABIN:
        raise BaenggipyoError(
            code='INVALID_ARGUMENT',
            message=f'네이버 국내선에는 {conditions.cabin}이 없습니다',
            hint='--cabin 일반석 또는 비즈니스석으로 찾으세요',
        )


def search_domestic_flights(
    client: NaverClient,
    legs: list[NaverLeg],
    *,
    conditions: NaverFlightConditions | None = None,
    sort: NaverFlightSort = '가격',
    limit: int = DEFAULT_LIMIT,
) -> NaverDomesticSearchResult:
    """국내선 편도·왕복 편 목록 (판매처 이름 1회 + 검색 SSE 1회, 왕복이면 오는 편 캐시 조회 1회 더).

    좌석 등급·항공사·정렬은 서버가 건다. 국내선은 모두 직항이다.
    """
    conditions = conditions or NaverFlightConditions()
    check_domestic_conditions(conditions)
    legs = _legs_with_airports(legs)
    partners = partner_names(client)
    directions: list[DomesticDirection] = ['departure', 'arrival'] if len(legs) == 2 else ['departure']
    notes: list[str] = []
    results: list[NaverDomesticLegResult] = []
    version: int | None = None
    for index, direction in enumerate(directions):
        # 공항 코드는 자동완성 응답 값이라 검증 실패는 입력 오류가 아니다 (flight 는 ID 에서 만들어 입력 오류가 맞으니 감싸지 않는다)
        with built_from_response('네이버 국내선 검색'):
            payload = _search_payload(
                legs, conditions, direction=direction, sort=sort, limit=limit, initial=index == 0, version=version,
                airlines=conditions.airlines,
            )
            fresh_payload = _search_payload(
                legs, conditions, direction=direction, sort=sort, limit=limit, initial=True, airlines=conditions.airlines,
            )
        event = client.run_request_flight_domestic_search_flights(payload)
        if event.is_expired:
            # 가는 편을 받은 직후라 만료될 일이 거의 없지만, 그러면 새로 검색한다
            event = client.run_request_flight_domestic_search_flights(fresh_payload)
        status = event.status or NaverDomesticStatusModel()
        # 오는 편은 같은 검색의 version 으로 묻는다. __v 가 없으면 None(가장 새 캐시)으로 묻는다. version 0 은 인기 노선에서 먼저
        # 만료돼 다시 검색하게 된다 (김포→제주가 v32 일 때 v0 은 isExpired, 생략하면 v32, 2026-10-11 실측)
        version = status.version
        # 방향마다 따로 끊길 수 있어 어느 목록이 덜 받았는지 붙인다 (flight 와 같다)
        notes += [f'{DIRECTION_LABELS[direction]}: {note}' for note in _search_note(event.status)]
        leg = legs[index]
        itineraries = [
            _itinerary(flight, status, partners, legs=legs, conditions=conditions, direction=direction)
            for flight in event.flights
        ]
        results.append(NaverDomesticLegResult(
            direction=DIRECTION_LABELS[direction],
            origin=leg.origin.code,
            destination=leg.destination.code,
            date=leg.date,
            returned=len(itineraries),
            itineraries=itineraries,
        ))
        if len(event.flights) >= limit:
            notes.append(f'{DIRECTION_LABELS[direction]}이 더 있을 수 있습니다. 정렬 순서 앞의 {limit}편만 받았습니다 (--limit 최대 {MAX_LIMIT})')
    if len(legs) == 2:
        notes.append(
            '국내선 왕복은 가는 편과 오는 편을 하나씩 고릅니다. 가격은 편마다 따로이고, 두 편의 id 를 함께 flight 에 넣으면 왕복 예약 가격이 나옵니다'
        )
    if conditions.infants:
        notes.append('유아 운임은 네이버가 계산에 넣지 않아 total_price_krw 가 null 입니다')
    return NaverDomesticSearchResult(search=_summary(legs, conditions), legs=results, notes=notes)


def _find_flight(
    client: NaverClient,
    legs: list[NaverLeg],
    conditions: NaverFlightConditions,
    direction: DomesticDirection,
    itinerary_id: str,
    seat_class: str,
    value: str,
) -> tuple[NaverDomesticFlightModel, NaverDomesticStatusModel, bool]:
    """캐시에서 편 하나를 다시 받는다. 캐시가 없거나 만료됐으면 같은 조건으로 다시 검색한다.

    같은 항공사·좌석 등급으로 좁혀 받고 편 ID 로 찾는다. 편 ID 는 출발 일시(12자리)·출발·도착 공항 다음이 항공사 코드다.
    에러에는 사용자가 넣은 baenggipyo ID(`value`)를 싣는다. 네이버 편 ID 만 실으면 에이전트가 어느 후보인지 맞춰 보지 못한다.

    Returns:
        tuple[편, 검색 상태, 다시 검색했는지]: 캐시가 만료돼 다시 검색했으면 True (가격이 바뀌었을 수 있다고 알린다).

    Raises:
        FareUnavailableError: 다시 찾아도 편이 없을 때.
        SearchIncompleteError: 검색이 시간 상한에 끊겨 편을 확인하지 못했을 때.
    """
    match = ITINERARY_AIRLINE_PATTERN.match(itinerary_id)
    airlines = [match.group(1)] if match else None
    event = client.run_request_flight_domestic_search_flights(_search_payload(
        legs, conditions, direction=direction, sort='가격', limit=DETAIL_FETCH_LIMIT, initial=False, version=CACHE_LATEST_VERSION,
        seat_classes=[seat_class], airlines=airlines,
    ))
    researched = bool(event.is_expired)
    if researched:
        event = client.run_request_flight_domestic_search_flights(_search_payload(
            legs, conditions, direction=direction, sort='가격', limit=DETAIL_FETCH_LIMIT, initial=True,
            seat_classes=[seat_class], airlines=airlines,
        ))
    flight = next((flight for flight in event.flights if flight.itinerary_id == itinerary_id and flight.seat_class == seat_class), None)
    if flight is None:
        # 시간 상한에 끊긴 검색에 없는 것은 매진이 아니다 (국제선 get_flight 와 같다)
        incomplete = event.status is not None and cut_short(event.status.is_complete)
        raise fare_missing_error('naver', value, cut_short=incomplete, reason='NOT_IN_RESULTS', alternative='skyscanner')
    return flight, event.status or NaverDomesticStatusModel(), researched


def _booking_params(flight: NaverDomesticFlightModel, fare: NaverDomesticFareModel) -> NaverDomesticBookingParamsModel:
    """번들의 판매처 조회 값과 같게: 편 정보와 운임 필드를 옮긴다."""
    segment = flight.segment
    return NaverDomesticBookingParamsModel(
        airline_code=segment.airline_code,
        fit_name=segment.flight_number or '',
        dep_city=segment.departure.airport_code,
        arr_city=segment.arrival.airport_code,
        departure_date_time=segment.departure.local_date_time or '',
        arrival_date_time=segment.arrival.local_date_time or '',
        seat_class=fare.booking_class,
        etc=fare.etc,
        seat_cnt=flight.seat_count,
        adult_fare=fare.adult_fare + fare.a_tax + fare.a_fuel,
        child_fare=fare.child_fare + fare.c_tax + fare.c_fuel,
        a_fuel=fare.a_fuel,
        c_fuel=fare.c_fuel,
        a_tax=fare.a_tax,
        c_tax=fare.c_tax,
        publish_fee=fare.publish_fee,
    )


def _pairs_total(pairs: list[tuple[NaverDomesticFlightModel, NaverDomesticFareModel]], conditions: NaverFlightConditions) -> int | None:
    """판매처 하나에서 고른 편 전부를 살 때 모든 승객 합계. 한 편이라도 합계를 모르면 None (`fare_total`)."""
    total = 0
    for _, fare in pairs:
        fare_sum = fare_total(fare, conditions)
        if fare_sum is None:
            return None
        total += fare_sum
    return total


def _ota_fare(pairs: list[tuple[NaverDomesticFlightModel, NaverDomesticFareModel]], conditions: NaverFlightConditions) -> NaverDomesticOtaFareModel:
    # 판매처 조회에는 사이트가 보내는 값을 그대로 보낸다 (유아는 빼고, 소아 운임이 없는 판매처도 그대로 더한다). 서버는 이 값을
    # otaList 의 fare 로 돌려주므로(2026-10-11 실측), 합계를 모르는 판매처의 출력 가격은 _pairs_total 로 다시 가린다
    total = 0
    for _, fare in pairs:
        adult, child = fare_per_person(fare)
        total += adult * conditions.adults + child * conditions.children
    return NaverDomesticOtaFareModel(
        ota_id=pairs[0][1].agt_code,
        support_n_pay=all(fare.support_n_pay for _, fare in pairs),
        fare=total,
        orig_fare=total,
        booking_params=[_booking_params(flight, fare) for flight, fare in pairs],
    )


def _offer(ota: NaverOtaModel, fare_name: str, per_adult: int | None, *, total_known: bool) -> NaverOffer:
    return NaverOffer(
        seller=ota.exp_name,
        seller_type=seller_type_of(ota.ota_type),
        # ota.fare 는 보낸 합계를 그대로 돌려준 값이라, 유아가 있거나 소아 운임이 없으면 일부 승객이 빠진 값이다
        total_price_krw=ota.fare if total_known else None,
        price_per_adult_krw=per_adult,
        fare_name=fare_name,
        url=ota.booking_url,
    )


def get_domestic_flights(
    client: NaverClient,
    values: list[str],
    *,
    offer_limit: int = DEFAULT_OFFER_LIMIT,
    url_limit: int | None = None,
) -> tuple[NaverDomesticBooking, list[str]]:
    """고른 국내선 편(편도 1개 또는 가는 편 + 오는 편)의 판매처별 가격과 예약 링크.

    편마다 캐시 조회 1회(만료됐으면 검색 1회 더) + 판매처 조회 1회. 판매처마다 고른 편 전부를 파는 운임을 묶고(사이트와 같다),
    왕복에서 두 편의 최저가 판매처가 다르면 그 조합도 넘긴다. `url_limit` 을 주면 앞의 그만큼 운임에만 예약 링크를 붙인다 (`shown_offers`).

    Raises:
        BaenggipyoError: ID 가 서로 다른 검색이거나, 같은 방향을 두 번 줬거나, 왕복 검색의 편을 하나만 줬을 때 (`INVALID_ARGUMENT`).
        FareUnavailableError: 다시 찾아도 편이 없을 때.
        SearchIncompleteError: 검색이 시간 상한에 끊겨 편을 확인하지 못했을 때 (`UPSTREAM_ERROR`, `reason: SEARCH_INCOMPLETE`).
    """
    # 에러에 넣은 ID 를 그대로 싣도록 원래 값을 끝에 붙여 둔다
    parsed = [(*parse_domestic_flight_id(value), value) for value in values]
    legs, conditions = parsed[0][0], parsed[0][1]
    if any(item[0] != legs or item[1] != conditions for item in parsed) or len({item[2] for item in parsed}) != len(parsed):
        raise BaenggipyoError(
            code='INVALID_ARGUMENT',
            message='국내선 편 ID 는 같은 검색의 가는 편 하나와 오는 편 하나만 함께 넣을 수 있습니다',
            hint='naver search flights 결과의 legs 에서 가는 편·오는 편 id 를 하나씩 고르세요',
        )
    if len(parsed) != len(legs):
        # 왕복 검색의 편 하나만 주면 왕복 캐시의 편을 편도로 묻게 되어 결과 페이지(왕복)와 예약 조회(편도)가 섞인다
        raise BaenggipyoError(
            code='INVALID_ARGUMENT',
            message=f'왕복으로 검색한 편 ID 는 가는 편·오는 편 둘을 함께 넣어야 합니다 (받은 ID {len(parsed)}개)',
            hint='같은 검색 결과의 legs 에서 오는 편 id 도 골라 함께 넣거나, 한 편만 보려면 --return 없이 편도로 다시 검색하세요',
        )
    parsed.sort(key=lambda item: item[2] != 'departure')
    partners = partner_names(client)
    notes: list[str] = []
    found = [_find_flight(client, legs, conditions, direction, itinerary_id, seat_class, value)
             for _, _, direction, itinerary_id, seat_class, value in parsed]
    if any(researched for _, _, researched in found):
        notes.append('검색 결과가 만료돼(검색 뒤 약 15분) 같은 조건으로 다시 검색했습니다. 가격이 바뀌었을 수 있습니다')
    for (_, status, _), (_, _, direction, _, _, _) in zip(found, parsed):
        # 끊긴 캐시 조회·다시 검색에서 찾은 편은 응답한 판매처의 운임만 담겨 있다. 판매처 목록과 가격이 일부 판매처만으로 정해진다
        notes += [f'{DIRECTION_LABELS[direction]}: {note}' for note in _search_note(status, missing='더 싼 판매처 운임')]
    flights = [flight for flight, _, _ in found]
    itineraries = [
        _itinerary(flight, status, partners, legs=legs, conditions=conditions, direction=direction)
        for (flight, status, _), (_, _, direction, _, _, _) in zip(found, parsed)
    ]
    # 판매처마다 고른 편 전부를 파는 운임 (편마다 그 판매처의 최저 운임)
    per_agent: dict[str, list[tuple[NaverDomesticFlightModel, NaverDomesticFareModel]]] = {}
    for flight in flights:
        cheapest_by_agent: dict[str, NaverDomesticFareModel] = {}
        for fare in flight.fares:
            if fare.agt_code not in cheapest_by_agent or fare_per_person(fare)[0] < fare_per_person(cheapest_by_agent[fare.agt_code])[0]:
                cheapest_by_agent[fare.agt_code] = fare
        for agt_code, fare in cheapest_by_agent.items():
            per_agent.setdefault(agt_code, []).append((flight, fare))
    whole = {agt: pairs for agt, pairs in per_agent.items() if len(pairs) == len(flights)}
    # 운임·편 정보는 검색 응답 값이라 검증 실패는 입력 오류가 아니다
    with built_from_response('네이버 국내선 판매처 조회'):
        ota_fares = [_ota_fare(pairs, conditions) for pairs in whole.values()]
        combined_bests: list[NaverDomesticFareModel] = []
        if len(flights) == 2:
            # 사이트처럼 두 편의 최저가 판매처가 다를 때만 따로 예약하는 조합을 넘긴다
            cheapest = [best for flight in flights if (best := _cheapest(flight, partners)) is not None]
            if len(cheapest) == 2 and cheapest[0].agt_code != cheapest[1].agt_code:
                combined_bests = cheapest
        combined_fares = [_ota_fare([(flight, best)], conditions) for flight, best in zip(flights, combined_bests)]
        payload = NaverBookingOptionsDomesticRequestPayloadModel(
            search_params=NaverDomesticBookingSearchParamsModel(
                itinerary=[
                    NaverBookingItineraryModel(
                        departure_airport=flight.segment.departure.airport_code,
                        arrival_airport=flight.segment.arrival.airport_code,
                        departure_date=flight.segment.departure.date or '',
                    )
                    for flight in flights
                ],
                person=NaverBookingPersonModel(adult=conditions.adults, child=conditions.children, infant=conditions.infants),
                trip='RT' if len(flights) == 2 else 'OW',
            ),
            ota_fares=ota_fares,
            combined_fares=combined_fares,
            schedule_ids=[
                '-'.join([flight.segment.airline_code, flight.segment.flight_number or '', flight.segment.departure.local_date_time or '', flight.seat_class])
                for flight in flights
            ],
        )
    response = client.run_request_booking_options_domestic(payload)
    options = unwrap_graphql(response, response.data.booking_options if response.data else None, '국내선 판매처')
    per_adult = {agt: sum(fare_per_person(fare)[0] for _, fare in pairs) for agt, pairs in whole.items()}
    total_known = {agt: _pairs_total(pairs, conditions) is not None for agt, pairs in whole.items()}
    offers = [
        _offer(ota, '성인/모든 결제수단', per_adult.get(ota.ota_id), total_known=total_known.get(ota.ota_id, False))
        for ota in options.ota_list if ota.active is None or ota.active.realtime is not False
    ]
    # 따로 예약하는 조합은 편마다 한 줄씩 온다. 넘긴 순서(가는 편, 오는 편)와 판매처로 어느 편인지 붙인다
    direction_by_agent = {fare.ota_id: label for fare, label in zip(combined_fares, ('가는 편', '오는 편'))}
    combined_known = {best.agt_code: fare_total(best, conditions) is not None for best in combined_bests}
    combined = [
        _offer(ota, f"{direction_by_agent.get(ota.ota_id, '한 편')}만 (다른 편은 다른 판매처에서 따로 예약)", None,
               total_known=combined_known.get(ota.ota_id, False))
        for ota in options.combined if ota.active is None or ota.active.realtime is not False
    ]
    # 판매처 운임을 (합계, 성인 1인 요금, 조합인지, 줄) 로 묶어 싼 순으로 세운다. 합계를 모르면(유아, 소아 운임 없는 판매처) 성인 1인 요금으로
    # 세워 --limit 이 1인 요금이 싼 판매처를 남기게 한다. 같은 값이면 한 판매처 운임을 앞에 둔다
    groups: list[tuple[int | None, int | None, bool, list[NaverOffer]]] = [
        (offer.total_price_krw, offer.price_per_adult_krw, False, [offer]) for offer in offers
    ]
    # 따로 예약하는 조합은 두 줄을 모두 예약해야 하는 운임 하나다. --limit 이 줄을 가르지 않게 묶어 세고, 실시간이 아니라 한 줄이 빠진 조합은
    # 내지 않는다 (남은 줄만으로는 다른 편을 어디서 사는지 모른다). 조합은 두 편 최저가의 합이라 대개 가장 싸고 대표 가격(total_price_krw)과
    # 같으므로, 뒤에 따로 두지 않고 조합 합계로 함께 세운다 (뒤에 두면 --limit 에 잘려 대표 가격의 운임이 offers 에 없다. skyscanner `_offers` 와 같다)
    if combined and len(combined) == len(combined_fares):
        combined_total = None if any(offer.total_price_krw is None for offer in combined) else sum(offer.total_price_krw or 0 for offer in combined)
        groups.append((combined_total, sum(fare_per_person(best)[0] for best in combined_bests), True, combined))
    groups.sort(key=lambda group: (group[0] is None, group[0] or 0, group[1] is None, group[1] or 0, group[2]))
    fares = [rows for _, _, _, rows in groups]
    # 국제선처럼 사이트를 따라 뺀 판매처 수를 알린다. 알리지 않으면 판매처가 모두 빠졌을 때 빈 offers 가 파는 곳이 없다고 읽힌다
    hidden = {ota.ota_id for ota in [*options.ota_list, *options.combined] if ota.active is not None and ota.active.realtime is False}
    if hidden:
        notes.append(f'실시간 가격을 주지 않는 판매처 {len(hidden)}곳은 사이트처럼 뺐습니다' + (' (남은 판매처가 없어 offers 가 비었습니다)' if not fares else ''))
    if len(fares) > offer_limit:
        notes.append(f'판매처 운임 {len(fares)}개 중 앞의 {offer_limit}개만 보여 줍니다 (--limit 최대 {MAX_OFFER_LIMIT})')
    totals = [itinerary.total_price_krw for itinerary in itineraries]
    if conditions.infants:
        notes.append('유아 운임은 네이버가 주지 않아 total_price_krw 가 null 입니다 (price_per_adult_krw 는 성인 1인 요금)')
    booking = NaverDomesticBooking(
        trip_type='왕복' if len(flights) == 2 else '편도',
        url=domestic_results_url(legs, conditions),
        flights=itineraries,
        total_price_krw=None if any(total is None for total in totals) else sum(total or 0 for total in totals),
        offer_count=len(fares),
        offers=shown_offers(fares, offer_limit, url_limit),
    )
    return booking, notes
