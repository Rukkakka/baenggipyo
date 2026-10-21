"""공항·도시 이름과 IATA 코드 해석 (`search airports`, 다른 명령의 출발·도착지)."""

from baenggipyo.core.errors import RouteNotSupportedError
from baenggipyo.core.flight import (
    Airport,
    AirportSearchResult,
    pick_place,
    resolve_inputs,
    resolve_route,
)
from baenggipyo.providers.naver.api.client import NaverClient
from baenggipyo.providers.naver.api.request.airport import (
    NaverAutocompleteLocationsRequestPayloadModel,
)
from baenggipyo.providers.naver.api.response.airport import NaverLocationModel
from baenggipyo.providers.naver.service.common import (
    NaverLeg,
    NaverLocation,
    trip_type_of,
    unwrap_graphql,
)

from typing import Literal


AIRPORT_TYPENAME: str = 'SearchAirport'
CITY_TYPENAME: str = 'SearchCity'


def _autocomplete(client: NaverClient, query: str) -> list[NaverLocationModel]:
    response = client.run_request_autocomplete_locations(NaverAutocompleteLocationsRequestPayloadModel(query=query))
    data = unwrap_graphql(response, response.data, '공항 검색')
    # 나라·대륙 후보와 코드가 없는 후보는 검색에 쓸 수 없다 (빈 코드가 요청 모델에 들어가 입력 오류처럼 보이지 않게 여기서 뺀다)
    return [
        location for location in data.autocomplete_locations or []
        if (location.typename == AIRPORT_TYPENAME and location.airport_code)
        or (location.typename == CITY_TYPENAME and location.city_code)
    ]


def _to_airport(location: NaverLocationModel) -> Airport:
    if location.typename == AIRPORT_TYPENAME:
        return Airport(
            code=location.airport_code or '',
            type='공항',
            name=location.airport_name,
            city=location.city_name,
            country=location.country_name,
            domestic=location.is_domestic,
        )
    return Airport(
        code=location.city_code or '',
        type='도시',
        name=location.city_name,
        city=location.city_name,
        country=location.country_name,
        airports=location.airports,
        domestic=location.is_domestic,
    )


def _to_location(location: NaverLocationModel) -> NaverLocation:
    if location.typename == AIRPORT_TYPENAME:
        return NaverLocation(code=location.airport_code or '', type='airport', name=location.airport_name, domestic=location.is_domestic)
    return NaverLocation(code=location.city_code or '', type='city', name=location.city_name, domestic=location.is_domestic,
                         airports=location.airports)


def search_airports(client: NaverClient, query: str, *, limit: int) -> AirportSearchResult:
    """이름이나 코드로 공항·도시 후보를 찾는다 (요청 1회). 도시가 그 도시의 공항보다 먼저 온다."""
    airports = [_to_airport(location) for location in _autocomplete(client, query)][:limit]
    return AirportSearchResult(query=query, returned=len(airports), airports=airports)


def _resolve_location(client: NaverClient, text: str) -> NaverLocation:
    return _to_location(pick_place(text, _autocomplete(client, text.strip()), _to_airport))


def resolve_locations(client: NaverClient, *texts: str) -> list[NaverLocation]:
    """출발·도착지 입력들을 검색에 넣을 공항 또는 도시로 정한다 (입력마다 요청 1회, 같은 입력은 한 번).
    고르는 규칙은 `core.flight.pick_place` (스카이스캐너와 같다). 도시 코드(SEL, TYO, OSA)는 그 도시의 모든 공항을 찾는다.

    Raises:
        AirportNotFoundError: 후보가 없거나, IATA 가 아닌 4글자 코드일 때 (모든 입력을 요청 전에 거른다).
        AirportAmbiguousError: 이름에 맞는 후보가 여럿일 때.
    """
    return resolve_inputs(texts, lambda text: _resolve_location(client, text))


def resolve_legs(client: NaverClient, raw_legs: list[tuple[str, str, str]]) -> list[NaverLeg]:
    """(출발, 도착, YYYY-MM-DD) 구간들을 해석한다 (`resolve_locations` 와 같은 규칙)."""
    return [
        NaverLeg(origin=origin, destination=destination, date=date)
        for origin, destination, date in resolve_route(raw_legs, lambda text: _resolve_location(client, text))
    ]


def route_kind(legs: list[NaverLeg]) -> Literal['international', 'domestic']:
    """국내선(모든 구간의 출발·도착이 국내)인지 국제선인지. 네이버는 둘을 다른 API 로 찾는다.

    Raises:
        RouteNotSupportedError: 국내선 구간과 국제선 구간이 섞였거나, 국내선 다구간일 때 (네이버 국내선은 편도·왕복만).
    """
    domestic = [bool(leg.origin.domestic and leg.destination.domestic) for leg in legs]
    route = '/'.join(f'{leg.origin.code}-{leg.destination.code}' for leg in legs)
    if all(domestic):
        if trip_type_of(legs) == '다구간':
            raise RouteNotSupportedError('naver', route, '국내선 다구간', 'skyscanner')
        return 'domestic'
    if any(domestic):
        raise RouteNotSupportedError('naver', route, '국내선과 국제선이 섞인', 'skyscanner')
    return 'international'
