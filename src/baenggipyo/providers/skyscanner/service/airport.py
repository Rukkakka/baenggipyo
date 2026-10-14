"""공항·도시 이름과 IATA 코드 해석 (`search airports`, 다른 명령의 출발·도착지)."""

from baenggipyo.core.flight import (
    Airport,
    AirportSearchResult,
    pick_place,
    resolve_route,
)
from baenggipyo.providers.skyscanner.api.client import SkyscannerClient
from baenggipyo.providers.skyscanner.api.request.airport import (
    SkyscannerAutosuggestSearchSearchFlightV1RequestParameterModel,
)
from baenggipyo.providers.skyscanner.api.response.airport import (
    SkyscannerAutosuggestPlaceModel,
)
from baenggipyo.providers.skyscanner.service.common import (
    KOREA_COUNTRY_ID,
    SkyscannerLeg,
    SkyscannerPlace,
)


def _is_city(place: SkyscannerAutosuggestPlaceModel) -> bool:
    if place.city_id:
        return place.place_id == place.city_id
    # CityId 가 빈 도시 후보가 있다 ('부산' 의 PUSA, 2026-10-12 실측). 도시는 GeoId 가 자기 컨테이너다
    return bool(place.geo_id) and place.geo_id == place.geo_container_id


def _is_country(place: SkyscannerAutosuggestPlaceModel) -> bool:
    return place.place_id == place.country_id


def _code(place: SkyscannerAutosuggestPlaceModel) -> str:
    # 도시는 스카이 ID 가 IATA 뒤에 A 를 붙인 값(SELA)이라 IataCode 를 쓴다. 공항은 스카이 ID 가 IATA 코드다
    return (place.iata_code or place.place_id) if _is_city(place) else place.place_id


def _autosuggest(client: SkyscannerClient, query: str) -> list[SkyscannerAutosuggestPlaceModel]:
    response = client.run_request_autosuggest_search_search_flight_v1(
        SkyscannerAutosuggestSearchSearchFlightV1RequestParameterModel(query=query)
    )
    # 나라 후보와 엔티티 ID 가 없는 후보는 검색에 쓸 수 없다. 같은 엔티티가 거듭 오기도 해('서울' 은 SELA 가 3번, 2026-10-12 실측) 처음 것만 둔다
    places: list[SkyscannerAutosuggestPlaceModel] = []
    seen: set[str] = set()
    for place in response.root:
        if _is_country(place) or not place.geo_id or place.geo_id in seen:
            continue
        seen.add(place.geo_id)
        places.append(place)
    return places


def _to_airport(place: SkyscannerAutosuggestPlaceModel) -> Airport:
    return Airport(
        code=_code(place),
        type='도시' if _is_city(place) else '공항',
        name=place.place_name,
        city=place.city_name,
        country=place.country_name,
        domestic=place.country_id == KOREA_COUNTRY_ID if place.country_id else None,
    )


def _to_place(place: SkyscannerAutosuggestPlaceModel) -> SkyscannerPlace:
    return SkyscannerPlace(
        code=_code(place),
        type='city' if _is_city(place) else 'airport',
        sky_id=place.place_id,
        entity_id=place.geo_id or '',
        name=place.place_name,
        domestic=place.country_id == KOREA_COUNTRY_ID if place.country_id else None,
    )


def search_airports(client: SkyscannerClient, query: str, *, limit: int) -> AirportSearchResult:
    """이름이나 코드로 공항·도시 후보를 찾는다 (요청 1회). 도시의 `airports` 는 스카이스캐너가 주지 않아 null 이다."""
    airports = [_to_airport(place) for place in _autosuggest(client, query)][:limit]
    return AirportSearchResult(query=query, returned=len(airports), airports=airports)


def resolve_place(client: SkyscannerClient, text: str) -> SkyscannerPlace:
    """출발·도착지 입력을 검색에 넣을 공항 또는 도시로 정한다 (요청 1회). 고르는 규칙은 `core.flight.pick_place` (네이버와 같다).

    Raises:
        AirportNotFoundError: 후보가 없을 때.
        AirportAmbiguousError: 이름에 맞는 후보가 여럿일 때.
    """
    return _to_place(pick_place(text, _autosuggest(client, text.strip()), _to_airport))


def resolve_legs(client: SkyscannerClient, raw_legs: list[tuple[str, str, str]]) -> list[SkyscannerLeg]:
    """(출발, 도착, YYYY-MM-DD) 구간들을 해석한다. 같은 입력은 한 번만 묻는다 (입력마다 요청 1회)."""
    return [
        SkyscannerLeg(origin=origin, destination=destination, date=date)
        for origin, destination, date in resolve_route(raw_legs, lambda text: resolve_place(client, text))
    ]
