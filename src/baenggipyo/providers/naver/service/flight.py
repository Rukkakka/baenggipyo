"""항공권 검색과 판매처별 가격 (`search flights`, `flight`)."""

import re

from pydantic import Field

from baenggipyo.core.errors import (
    BaenggipyoError,
    FareUnavailableError,
    fare_missing_error,
)
from baenggipyo.core.flight import (
    Journey,
    SearchLeg,
    SearchSummary,
    Segment,
    compact_datetime,
    seconds_to_minutes,
    shown_offers,
)
from baenggipyo.core.schema import built_from_response
from baenggipyo.providers.naver.api.client import NaverClient
from baenggipyo.providers.naver.api.request.common import (
    NaverBookingItineraryModel,
    NaverBookingPersonModel,
    NaverFlightFilterModel,
)
from baenggipyo.providers.naver.api.request.flight import (
    NaverBookingOptionsRequestPayloadModel,
    NaverBookingSearchParamsModel,
    NaverFlightInternationalSearchFlightsRequestPayloadModel,
    NaverOtaFareModel,
    NaverSearchItineraryModel,
)
from baenggipyo.providers.naver.api.response.flight import (
    NaverFareMappingModel,
    NaverFareModel,
    NaverFlightInternationalSearchFlightsResponseModel,
    NaverItineraryModel,
    NaverSearchStatusModel,
)
from baenggipyo.providers.naver.service.common import (
    CABIN_BY_SEAT_CLASS,
    CACHE_LATEST_VERSION,
    CARD_FARE_PREFIX,
    DEFAULT_LIMIT,
    DEFAULT_OFFER_LIMIT,
    GENERAL_FARE_TYPE,
    MAX_LIMIT,
    MAX_OFFER_LIMIT,
    SEAT_CLASS_BY_CABIN,
    SORT_BY_LABEL,
    TRIP_CODE_BY_TYPE,
    NaverFlightConditions,
    NaverFlightSearchResult,
    NaverFlightSort,
    NaverItinerary,
    NaverLeg,
    NaverLocation,
    NaverOffer,
    cut_short,
    flight_number_of,
    incomplete_note,
    partner_names,
    results_url,
    seller_type_of,
    trip_type_of,
    unwrap_graphql,
)

from typing import (
    Annotated,
    Literal,
)


BAGGAGE_BY_FEE_TYPE: dict[str, bool] = {
    'FREE': True,
    'CHARGE': False,
}


LEG_ID_PATTERN: str = r'[A-Z]{3}[ac][A-Z]{3}[ac]\d{8}'


FLIGHT_ID_PATTERN: re.Pattern[str] = re.compile(
    r'^(?P<seat>[YPCF])-(?P<adults>\d)(?P<children>\d)(?P<infants>\d)-(?P<direct>[01])-'
    rf'(?P<legs>{LEG_ID_PATTERN}(?:_{LEG_ID_PATTERN})*)~(?P<itinerary_ids>[0-9A-Z+-]+)$'
)


LEG_ID_PARTS: re.Pattern[str] = re.compile(r'([A-Z]{3})([ac])([A-Z]{3})([ac])(\d{4})(\d{2})(\d{2})')


LOCATION_TYPE_BY_LETTER: dict[str, Literal['airport', 'city']] = {
    'a': 'airport',
    'c': 'city',
}


class NaverItineraryDetail(NaverItinerary):
    """`flight` 결과: 항공권 한 건과 판매처별 가격."""

    offers: Annotated[
        list[NaverOffer],
        Field(description='판매처별 가격. 모든 결제수단 운임을 싼 순으로 먼저, 결제수단 조건이 있는 운임을 싼 순으로 뒤에')
    ]


def _adult_fare(fare: NaverFareModel | None) -> int | None:
    return fare.adult.total_fare if fare is not None and fare.adult is not None else None


def _total_fare(fare: NaverFareModel, conditions: NaverFlightConditions) -> int | None:
    """모든 승객 합계. 승객 유형의 1인 운임이 하나라도 없으면 None (추측하지 않는다)."""
    total = 0
    for passenger, count in ((fare.adult, conditions.adults), (fare.child, conditions.children), (fare.infant, conditions.infants)):
        if count == 0:
            continue
        if passenger is None or passenger.total_fare is None:
            return None
        total += passenger.total_fare * count
    return total


def general_fare(fares: list[NaverFareModel]) -> NaverFareModel | None:
    """대표 운임: 모든 결제수단(A01) 운임 중 성인 1인 최저가. 조건이 붙은 할인 운임은 대표로 쓰지 않는다."""
    candidates = [fare for fare in fares if fare.fare_type == GENERAL_FARE_TYPE and _adult_fare(fare) is not None]
    return min(candidates, key=lambda fare: _adult_fare(fare) or 0, default=None)


def card_discount_fare(fares: list[NaverFareModel], general: NaverFareModel | None) -> NaverFareModel | None:
    """대표 운임보다 싼 카드·간편결제 할인 운임 중 최저가. 없으면 None."""
    ceiling = _adult_fare(general)
    candidates = [
        fare for fare in fares
        if fare.fare_type.startswith(CARD_FARE_PREFIX)
        and (price := _adult_fare(fare)) is not None
        and (ceiling is None or price < ceiling)
    ]
    return min(candidates, key=lambda fare: _adult_fare(fare) or 0, default=None)


def _baggage(fare: NaverFareModel | None) -> bool | None:
    return BAGGAGE_BY_FEE_TYPE.get(fare.baggage_fee_type or '') if fare is not None else None


def _journey(itinerary: NaverItineraryModel, airlines: dict[str, str]) -> Journey:
    segments = [
        Segment(
            airline_code=segment.marketing_carrier.airline_code,
            airline=airlines.get(segment.marketing_carrier.airline_code),
            flight_number=flight_number_of(segment.marketing_carrier.airline_code, segment.marketing_carrier.flight_number),
            operating_airline_code=segment.operating_carrier.airline_code if segment.operating_carrier else None,
            operating_airline=airlines.get(segment.operating_carrier.airline_code) if segment.operating_carrier else None,
            departure_airport=segment.departure.airport_code,
            arrival_airport=segment.arrival.airport_code,
            departure_at=compact_datetime(segment.departure.local_date_time),
            arrival_at=compact_datetime(segment.arrival.local_date_time),
            duration_minutes=seconds_to_minutes(segment.flight_duration),
            # 기종을 모르면 빈 문자열로 준다 (2026-10-15 ICN→KIX 10편 중 5편). 모름은 null 로 낸다
            aircraft=segment.aircraft_code or None,
        )
        for segment in itinerary.segments
    ]
    return Journey(
        departure_airport=segments[0].departure_airport,
        arrival_airport=segments[-1].arrival_airport,
        departure_at=segments[0].departure_at,
        arrival_at=segments[-1].arrival_at,
        duration_minutes=seconds_to_minutes(itinerary.duration),
        stops=len(segments) - 1,
        stopover_airports=[segment.arrival_airport for segment in segments[:-1]],
        segments=segments,
    )


def _cheapest_seller(
    general: NaverFareModel | None,
    fares: list[NaverFareModel],
    partners: dict[str, str],
    conditions: NaverFlightConditions,
) -> str | None:
    """대표 운임 `general`(`_price_fields`)의 판매처 이름 (`search flights`). `flight` 는 판매처 조회가 준 이름을 쓴다."""
    if general is None:
        return None
    if general.partner_code in partners:
        return partners[general.partner_code]
    # 판매처 이름표(bookingPartners)에 없는 판매처가 있다 (26곳뿐). 대표 운임과 성인 운임·승객 합계·수하물이 모두 같은 운임이
    # 이름을 아는 판매처에도 있으면 그 이름만 쓴다. 가격·수하물은 대표 운임 값 그대로다
    named = next(
        (fare for fare in fares if fare.fare_type == GENERAL_FARE_TYPE and fare.partner_code in partners
         and _adult_fare(fare) == _adult_fare(general) and _total_fare(fare, conditions) == _total_fare(general, conditions)
         and _baggage(fare) == _baggage(general)),
        None,
    )
    return partners[named.partner_code] if named else None


def _price_fields(
    fares: list[NaverFareModel],
    status: NaverSearchStatusModel,
    conditions: NaverFlightConditions,
) -> tuple[NaverFareModel | None, dict[str, object]]:
    """대표 운임(`general_fare`)과, 그 운임과 카드 할인 운임으로 채우는 `NaverItinerary` 가격 필드."""
    general = general_fare(fares)
    discount = card_discount_fare(fares, general)
    discount_info = status.fare_types_code_map.get(discount.fare_type) if discount else None
    return general, {
        'total_price_krw': _total_fare(general, conditions) if general else None,
        'price_per_adult_krw': _adult_fare(general),
        'baggage_included': _baggage(general),
        'card_discount_price_krw': _adult_fare(discount),
        'card_discount_name': discount_info.name if discount_info else None,
        'card_discount_category': (discount_info.category or None) if discount_info else None,
    }


def _itinerary_fields(
    mapping: NaverFareMappingModel,
    itineraries: dict[str, NaverItineraryModel],
    status: NaverSearchStatusModel,
    *,
    legs: list[NaverLeg],
    conditions: NaverFlightConditions,
) -> dict[str, object] | None:
    """운임 묶음 하나의 여정 필드. 여정이 응답에 없으면 None.

    가격(`_price_fields`)·판매처 필드는 부르는 쪽이 붙인다. `flight` 는 판매처 조회에 낸 운임만으로 가격을 정한다.
    """
    journey_ids = mapping.itinerary_ids.split('-')
    if any(journey_id not in itineraries or not itineraries[journey_id].segments for journey_id in journey_ids):
        return None
    return {
        'source': 'naver',
        'id': flight_id(legs, conditions, mapping.itinerary_ids),
        'url': results_url(legs, adults=conditions.adults, children=conditions.children, infants=conditions.infants,
                           cabin=conditions.cabin, direct=conditions.direct, selected=mapping.itinerary_ids),
        'trip_type': trip_type_of(legs),
        'cabin': conditions.cabin,
        'journeys': [_journey(itineraries[journey_id], status.airlines_code_map) for journey_id in journey_ids],
    }


def flight_id(legs: list[NaverLeg], conditions: NaverFlightConditions, itinerary_ids: str) -> str:
    """`flight` 에 넘길 항공권 ID. 서버 캐시는 검색 조건이 키라서 조건을 함께 담는다.

    형식: `<좌석>-<성인><소아><유아>-<직항 0|1>-<구간>[_<구간>…]~<여정 ID>`, 구간은 `<출발><a|c><도착><a|c><YYYYMMDD>`
    (a 공항, c 도시). 예: `Y-100-0-ICNaNRTa20261115~20261115ICNNRTZE0605`.
    """
    leg_ids = '_'.join(
        f'{leg.origin.code}{leg.origin.type[0]}{leg.destination.code}{leg.destination.type[0]}{leg.compact_date}'
        for leg in legs
    )
    seat = SEAT_CLASS_BY_CABIN[conditions.cabin]
    return f'{seat}-{conditions.adults}{conditions.children}{conditions.infants}-{int(conditions.direct)}-{leg_ids}~{itinerary_ids}'


def parse_flight_id(value: str) -> tuple[list[NaverLeg], NaverFlightConditions, str]:
    """`flight_id` 를 되돌린다.

    Raises:
        BaenggipyoError: 형식이 다를 때 (`INVALID_ARGUMENT`).
    """
    match = FLIGHT_ID_PATTERN.fullmatch(value.strip())
    if match is None:
        raise BaenggipyoError(
            code='INVALID_ARGUMENT',
            message=f'네이버 항공권 ID 형식이 아닙니다: {value}',
            hint='naver search flights 결과의 id 를 그대로 넣으세요 (예: Y-100-0-ICNaNRTa20261115~20261115ICNNRTZE0605)',
        )
    legs = []
    for origin, origin_type, destination, destination_type, year, month, day in LEG_ID_PARTS.findall(match['legs']):
        legs.append(NaverLeg(
            origin=NaverLocation(code=origin, type=LOCATION_TYPE_BY_LETTER[origin_type]),
            destination=NaverLocation(code=destination, type=LOCATION_TYPE_BY_LETTER[destination_type]),
            date=f'{year}-{month}-{day}',
        ))
    conditions = NaverFlightConditions(
        adults=int(match['adults']),
        children=int(match['children']),
        infants=int(match['infants']),
        cabin=CABIN_BY_SEAT_CLASS[match['seat']],
        direct=match['direct'] == '1',
    )
    return legs, conditions, match['itinerary_ids']


def _search_payload(
    legs: list[NaverLeg],
    conditions: NaverFlightConditions,
    *,
    sort: NaverFlightSort,
    limit: int,
    initial: bool,
    version: int | None = None,
    selected: list[str] | None = None,
) -> NaverFlightInternationalSearchFlightsRequestPayloadModel:
    flight_filter: dict[str, object] = {}
    if conditions.airlines:
        flight_filter['airlines'] = conditions.airlines
    if selected:
        flight_filter['selectedItineraries'] = selected
    return NaverFlightInternationalSearchFlightsRequestPayloadModel(
        trip_type=TRIP_CODE_BY_TYPE[trip_type_of(legs)],
        seat_class=SEAT_CLASS_BY_CABIN[conditions.cabin],
        adult_count=conditions.adults,
        child_count=conditions.children,
        infant_count=conditions.infants,
        is_nonstop=conditions.direct,
        initial_request=initial,
        version=version,
        itineraries=[
            NaverSearchItineraryModel(
                departure_location_code=leg.origin.code,
                departure_location_type=leg.origin.type,
                arrival_location_code=leg.destination.code,
                arrival_location_type=leg.destination.type,
                departure_date=leg.compact_date,
            )
            for leg in legs
        ],
        flight_filter=NaverFlightFilterModel(filter=flight_filter, limit=limit, sort=SORT_BY_LABEL[sort]),
    )


def _search_note(status: NaverSearchStatusModel | None) -> list[str]:
    if status is None:
        return []
    return incomplete_note(status.is_completed, status.completed_partner_count, status.requested_partner_count)


def search_flights(
    client: NaverClient,
    legs: list[NaverLeg],
    *,
    conditions: NaverFlightConditions | None = None,
    sort: NaverFlightSort = '가격',
    limit: int = DEFAULT_LIMIT,
) -> NaverFlightSearchResult[NaverItinerary]:
    """편도·왕복·다구간 항공권을 찾는다 (판매처 이름 1회 + 검색 SSE 1회, 9~17초).

    서버가 건 조건(직항·좌석·항공사)과 정렬은 다시 거르거나 다시 세우지 않는다. 대표 가격은 모든 결제수단 운임(A01) 최저가다.
    """
    conditions = conditions or NaverFlightConditions()
    # 공항·도시 코드는 자동완성 응답 값이라 검증 실패는 입력 오류가 아니다 (flight 는 ID 에서 만들어 입력 오류가 맞으니 감싸지 않는다)
    with built_from_response('네이버 항공권 검색'):
        payload = _search_payload(legs, conditions, sort=sort, limit=limit, initial=True)
    partners = partner_names(client)
    event = client.run_request_flight_international_search_flights(payload)
    status = event.status or NaverSearchStatusModel()
    notes = _search_note(event.status)
    itineraries = {itinerary.itinerary_id: itinerary for itinerary in event.itineraries}
    results: list[NaverItinerary] = []
    for mapping in event.fare_mappings:
        fields = _itinerary_fields(mapping, itineraries, status, legs=legs, conditions=conditions)
        if fields is None:
            continue
        general, price_fields = _price_fields(mapping.fares, status, conditions)
        results.append(NaverItinerary.model_validate({
            **fields,
            **price_fields,
            'seller_count': len({fare.partner_code for fare in mapping.fares}),
            'cheapest_seller': _cheapest_seller(general, mapping.fares, partners, conditions),
        }))
    missing = len(event.fare_mappings) - len(results)
    if missing:
        notes.append(f'여정 정보가 응답에 없는 항공권 {missing}개는 뺐습니다')
    no_general = [itinerary for itinerary in results if itinerary.price_per_adult_krw is None]
    card_only = sum(1 for itinerary in no_general if itinerary.card_discount_price_krw is not None)
    if card_only:
        notes.append(
            f'모든 결제수단으로 살 수 있는 운임이 없는 항공권 {card_only}개는 대표 가격이 null 입니다. '
            'card_discount_* 의 결제수단으로만 살 수 있습니다'
        )
    if len(no_general) > card_only:
        # 성인 운임이 빠졌거나 A01·카드 할인이 아닌 운임(장애인 할인 등)만 있다. 어떤 가격인지 모르므로 가격을 만들지 않는다
        notes.append(
            f'모든 결제수단 운임도 카드 할인 운임도 없는 항공권 {len(no_general) - card_only}개는 가격이 null 입니다. '
            '판매처별 운임은 flight 로 확인하세요'
        )
    if sort == '가격':
        prices = [itinerary.price_per_adult_krw for itinerary in results if itinerary.price_per_adult_krw is not None]
        if prices != sorted(prices):
            notes.append(
                '가격순은 네이버 기준(카드 할인을 포함한 최저가)이라 price_per_adult_krw(모든 결제수단 최저가) 순서와 조금 다를 수 있습니다'
            )
    if len(event.fare_mappings) >= limit:
        notes.append(f'조건에 맞는 항공권이 더 있을 수 있습니다. 정렬 순서 앞의 {limit}개만 받았습니다 (--limit 최대 {MAX_LIMIT})')
    summary = SearchSummary(
        trip_type=trip_type_of(legs),
        legs=[SearchLeg(origin=leg.origin.code, destination=leg.destination.code, date=leg.date) for leg in legs],
        cabin=conditions.cabin,
        adults=conditions.adults,
        children=conditions.children,
        infants=conditions.infants,
        direct=conditions.direct,
        url=results_url(legs, adults=conditions.adults, children=conditions.children, infants=conditions.infants,
                        cabin=conditions.cabin, direct=conditions.direct),
    )
    return NaverFlightSearchResult[NaverItinerary](search=summary, returned=len(results), itineraries=results, notes=notes)


def _cached_search(
    client: NaverClient,
    legs: list[NaverLeg],
    conditions: NaverFlightConditions,
    journey_ids: list[str],
    version: int | None,
) -> NaverFlightInternationalSearchFlightsResponseModel:
    """서버 캐시 조회. `version` 이 None 이면 그 조건의 가장 새 검색 결과를 준다 (`CACHE_LATEST_VERSION`)."""
    return client.run_request_flight_international_search_flights(
        _search_payload(legs, conditions, sort='가격', limit=1, initial=False, version=version, selected=journey_ids)
    )


def get_flight(
    client: NaverClient,
    value: str,
    *,
    offer_limit: int = DEFAULT_OFFER_LIMIT,
    url_limit: int | None = None,
) -> tuple[NaverItineraryDetail, list[str]]:
    """항공권 하나의 판매처별 가격과 예약 링크 (검색 캐시 조회 1회 + 판매처 1회. 캐시가 만료됐으면 검색 1회 더).

    `url_limit` 을 주면 앞의 그만큼 운임에만 예약 링크를 붙인다 (`shown_offers`).

    Returns:
        tuple[NaverItineraryDetail, list[str]]: 항공권과 판매처 목록, 결과에 붙일 안내.

    Raises:
        FareUnavailableError: 다시 검색해도 그 항공권이 없을 때 (매진·판매 종료).
        SearchIncompleteError: 검색이 시간 상한에 끊겨 그 항공권을 확인하지 못했을 때 (`UPSTREAM_ERROR`, `reason: SEARCH_INCOMPLETE`).
    """
    legs, conditions, itinerary_ids = parse_flight_id(value)
    journey_ids = itinerary_ids.split('-')
    notes: list[str] = []
    event = _cached_search(client, legs, conditions, journey_ids, version=CACHE_LATEST_VERSION)
    statuses = [event.status]
    if event.is_expired:
        # 서버 캐시는 검색 뒤 약 15분이다. 같은 조건으로 다시 검색해 캐시를 만든 뒤 다시 묻는다
        notes.append('검색 결과가 만료돼(검색 뒤 약 15분) 같은 조건으로 다시 검색했습니다. 가격이 바뀌었을 수 있습니다')
        fresh = client.run_request_flight_international_search_flights(
            _search_payload(legs, conditions, sort='가격', limit=1, initial=True, selected=journey_ids)
        )
        event = _cached_search(client, legs, conditions, journey_ids, version=fresh.status.version if fresh.status else CACHE_LATEST_VERSION)
        statuses = [fresh.status, event.status]
    # 진행 중인 검색의 캐시를 물으면 서버가 끝날 때까지 이어 보내므로(2026-10-11 실측 5초 뒤 완료) 끊긴 것은 시간 상한에 닿은 것뿐이다.
    # 다시 검색과 캐시 조회 중 먼저 끊긴 것을 본다
    cut = next((status for status in statuses if status is not None and cut_short(status.is_completed)), None)
    if event.is_expired:
        # 스트림을 끊으면 서버가 그 검색을 버려 바로 뒤 캐시 조회가 만료로 온다 (2026-10-11 실측: 2초에 끊은 검색, 판매처 11/20곳).
        # 그때는 매진이 아니라 늦게 끝난 것이다
        raise fare_missing_error('naver', value, cut_short=cut is not None, reason='SEARCH_EXPIRED', alternative='skyscanner')
    mapping = next((mapping for mapping in event.fare_mappings if mapping.itinerary_ids == itinerary_ids), None)
    if mapping is None or not mapping.fares:
        raise fare_missing_error('naver', value, cut_short=cut is not None, reason='NOT_IN_RESULTS', alternative='skyscanner')
    if cut is not None:
        # 끊긴 검색에서 찾은 항공권은 응답한 판매처의 운임만 담겨 있다. 판매처 목록과 대표 가격이 일부 판매처만으로 정해진다
        notes += incomplete_note(cut.is_completed, cut.completed_partner_count, cut.requested_partner_count, missing='더 싼 판매처 운임')
    status = event.status or NaverSearchStatusModel()
    itineraries = {itinerary.itinerary_id: itinerary for itinerary in event.itineraries}
    # 판매처 이름표(partner_names)는 받지 않는다 (요청 1회를 아낀다). 판매처 필드(cheapest_seller·seller_count)는 아래 판매처 조회로 채운다
    fields = _itinerary_fields(mapping, itineraries, status, legs=legs, conditions=conditions)
    if fields is None:
        raise FareUnavailableError('naver', value, reason='NO_ITINERARY')
    offers, shown_fares, sellers, not_realtime, unbookable = _offers(
        client, mapping, status, legs=legs, conditions=conditions, journeys=[itineraries[i] for i in journey_ids]
    )
    # 대표 가격도 offers 에 낸 운임으로 정한다. 실시간이 아니라 뺀 판매처나 판매처 조회에 넘기지 못한 운임이 가장 싸면, 검색 운임
    # 전체로 정한 가격은 offers 의 어느 판매처에서도 살 수 없다
    general, price_fields = _price_fields(shown_fares, status, conditions)
    if unbookable:
        notes.append(
            f'예약 링크나 승객 유형(소아·유아) 운임이 응답에 없는 판매처 운임 {unbookable}개는 판매처 조회에 넘기지 못해 뺐습니다'
            + (' (남은 운임이 없어 offers 가 비었습니다)' if not offers else '')
        )
    if not_realtime:
        notes.append(
            f'실시간 가격을 주지 않는 판매처 {not_realtime}곳은 사이트처럼 뺐습니다. 대표 가격도 남은 판매처로 정해 '
            'search flights 의 가격·seller_count 와 다를 수 있습니다'
        )
    if len(offers) > offer_limit:
        # 판매처 수와 운임 수를 함께 쓴다. 운임 수만 쓰면 seller_count 와 다른 수(판매처 23곳, 운임 133개)라 둘을 섞어 말하기 쉽다
        notes.append(f'판매처 {len(sellers)}곳의 운임 {len(offers)}개 중 앞의 {offer_limit}개만 보여 줍니다 (--limit 최대 {MAX_OFFER_LIMIT})')
    # seller_count 는 offers 에 낸 판매처 수다. 판매처마다 운임(A01·카드)이 여럿이라 offers 행 수와 다르다 (ZE605 판매처 19곳 운임 116개).
    # search flights 는 실시간 여부를 몰라 운임 묶음의 판매처를 모두 세므로 실시간이 아닌 판매처만큼 크다 (TW241 24곳 중 실시간 23곳, 2026-10-11)
    detail = NaverItineraryDetail.model_validate({
        **fields,
        **price_fields,
        'seller_count': len(sellers),
        'cheapest_seller': sellers.get(general.partner_code) if general else None,
        'offers': shown_offers([[offer] for offer in offers], offer_limit, url_limit),
    })
    return detail, notes


def _offers(
    client: NaverClient,
    mapping: NaverFareMappingModel,
    status: NaverSearchStatusModel,
    *,
    legs: list[NaverLeg],
    conditions: NaverFlightConditions,
    journeys: list[NaverItineraryModel],
) -> tuple[list[NaverOffer], list[NaverFareModel], dict[str, str | None], int, int]:
    """판매처별 가격 (getOtaList). 사이트처럼 실시간 판매처(`active.realtime`)만 낸다.

    Returns:
        tuple[list[NaverOffer], list[NaverFareModel], dict[str, str | None], int, int]: 판매처 운임, 그 운임의 검색 운임(대표 가격을 정할 때 쓴다),
            낸 판매처 코드 → 이름, 실시간이 아니라 뺀 판매처 수, 예약 URL·승객 합계가 없어 판매처 조회에 넘기지 못한 운임 수.
            판매처는 이름이 아니라 코드로 센다.

    Raises:
        BaenggipyoError: 판매처 목록(`bookingOptions`)이 빠진 응답일 때 (`UPSTREAM_ERROR`).
    """
    # 운임·여정 날짜는 검색 응답 값이라 검증 실패는 입력 오류가 아니다
    with built_from_response('네이버 판매처 조회'):
        ota_fares = [
            NaverOtaFareModel(
                ota_id=fare.partner_code,
                fare=total,
                fare_type=fare.fare_type,
                booking_url=fare.reservation_url,
            )
            for fare in mapping.fares
            # 합계를 모르는 운임(승객 유형 운임이 빠짐)과 예약 URL 이 없는 운임은 판매처 조회에 넘기지 않는다
            if fare.reservation_url and (total := _total_fare(fare, conditions)) is not None
        ]
        # 소아·유아 1명씩 넣은 ICN→NRT 캐시 조회에서는 128개 모두 URL·합계가 있었다 (2026-10-12 실측). 빠지면 get_flight 가 notes 로 알린다
        unbookable = len(mapping.fares) - len(ota_fares)
        if not ota_fares:
            return [], [], {}, 0, unbookable
        payload = NaverBookingOptionsRequestPayloadModel(
            search_params=NaverBookingSearchParamsModel(
                itinerary=[
                    NaverBookingItineraryModel(
                        departure_airport=journey.segments[0].departure.airport_code,
                        arrival_airport=journey.segments[-1].arrival.airport_code,
                        departure_date=(journey.segments[0].departure.local_date_time or '')[:8],
                    )
                    for journey in journeys
                ],
                person=NaverBookingPersonModel(adult=conditions.adults, child=conditions.children, infant=conditions.infants),
                trip=TRIP_CODE_BY_TYPE[trip_type_of(legs)],
                fare_type=SEAT_CLASS_BY_CABIN[conditions.cabin],
            ),
            ota_fares=ota_fares,
            schedule_ids=[journey.itinerary_id for journey in journeys],
            curations=mapping.curation,
        )
    response = client.run_request_booking_options(payload)
    # 판매처 목록이 빠진 응답을 빈 offers 로 내면 파는 곳이 없다고 읽힌다. 국내선 판매처 조회처럼 에러로 낸다
    options = unwrap_graphql(response, response.data.booking_options if response.data else None, '판매처')
    fare_by_key = {(fare.partner_code, fare.fare_type): fare for fare in mapping.fares}
    offers = []
    shown_fares: list[NaverFareModel] = []
    sellers: dict[str, str | None] = {}
    hidden: set[str] = set()
    for ota in options.ota_list:
        if ota.active is not None and ota.active.realtime is False:
            hidden.add(ota.ota_id)
            continue
        sellers[ota.ota_id] = ota.exp_name
        fare = fare_by_key.get((ota.ota_id, ota.fare_type or ''))
        if fare is not None:
            shown_fares.append(fare)
        info = status.fare_types_code_map.get(ota.fare_type or '')
        offers.append(NaverOffer(
            seller=ota.exp_name,
            seller_type=seller_type_of(ota.ota_type),
            total_price_krw=ota.fare,
            price_per_adult_krw=_adult_fare(fare),
            fare_name=info.name if info else ota.fare_type,
            payment_condition=(info.category or None) if info else None,
            baggage_included=_baggage(fare),
            url=ota.booking_url,
        ))
    # 대표 가격처럼 모든 결제수단 운임을 먼저 둔다. 카드 조건 운임은 대개 더 싸서 가격만으로 세우면 앞자리를 모두 차지한다
    offers.sort(key=lambda offer: (offer.payment_condition is not None, offer.total_price_krw is None, offer.total_price_krw or 0))
    return offers, shown_fares, sellers, len(hidden - sellers.keys()), unbookable
