"""어디든지: 출발지만 정하고 목적지별 최저가를 찾는다 (`explore`)."""

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from baenggipyo.core.flight import (
    Destination,
    TripType,
)
from baenggipyo.core.schema import built_from_response
from baenggipyo.providers.skyscanner.api.client import SkyscannerClient
from baenggipyo.providers.skyscanner.api.request.common import (
    SkyscannerDateModel,
    SkyscannerPlaceModel,
    SkyscannerRadarWebUnifiedSearchV2RequestPayloadModel,
    SkyscannerSearchLegModel,
)
from baenggipyo.providers.skyscanner.service.common import (
    ESTIMATE_NOTE,
    SkyscannerPlace,
    run_search,
    stopped_early,
)

from typing import Annotated


EXPLORE_DEFAULT_LIMIT: int = 20
EXPLORE_MAX_LIMIT: int = 100
CITY_LOCATION_TYPE: str = 'City'


class SkyscannerExploreResult(BaseModel):
    """`explore` 결과."""

    model_config = ConfigDict(
        extra='forbid',
    )

    origin: Annotated[
        str,
        Field(description='출발 코드')
    ]
    trip_type: Annotated[
        TripType,
        Field(description='편도 또는 왕복')
    ]
    month: Annotated[
        str | None,
        Field(description='출발 달 (YYYY-MM). 주지 않았으면 언제든')
    ] = None
    returned: Annotated[
        int,
        Field(description='돌려준 목적지 수')
    ]
    destinations: Annotated[
        list[Destination],
        Field(description='목적지 (싼 순)')
    ]
    notes: Annotated[
        list[str],
        Field(exclude=True, description='결과 안내')
    ] = []


def _dates(month: str | None) -> SkyscannerDateModel:
    if month is None:
        return SkyscannerDateModel(type='anytime')
    return SkyscannerDateModel(type='month', year=month[:4], month=month[5:7])


def explore(
    client: SkyscannerClient,
    origin: SkyscannerPlace,
    *,
    month: str | None = None,
    round_trip: bool = False,
    direct: bool = False,
    limit: int = EXPLORE_DEFAULT_LIMIT,
) -> SkyscannerExploreResult:
    """도시별 지표 최저가를 싼 순으로 (검색 1회, 바로 끝난다).

    목적지를 `everywhere` 로, 결과 단위를 도시(`targetGeoLevel: city`)로 묻는다. 왕복은 같은 달의 돌아오는 구간을 하나 더 넣는다.
    광고(`ADVERT`)와 나라 단위 항목은 뺀다. `direct` 면 직항 지표 가격(`flightQuotes.direct`)을 쓰고, 없는 도시는 뺀다.
    """
    # 엔티티 ID 는 자동완성 응답 값이라 검증 실패는 입력 오류가 아니다 (달은 CLI 가 검사했다)
    with built_from_response('스카이스캐너 어디든지'):
        origin_place = SkyscannerPlaceModel(type='entity', entity_id=origin.entity_id)
        everywhere = SkyscannerPlaceModel(type='everywhere')
        legs = [SkyscannerSearchLegModel(leg_origin=origin_place, leg_destination=everywhere, dates=_dates(month))]
        if round_trip:
            legs.append(SkyscannerSearchLegModel(leg_origin=everywhere, leg_destination=origin_place, dates=_dates(month)))
        payload = SkyscannerRadarWebUnifiedSearchV2RequestPayloadModel(legs=legs, target_geo_level='city')
    response, completed, limited = run_search(client, payload)
    found = response.everywhere_destination
    # 어디든지 결과의 상태는 결과 안의 context 다 (바로 complete 로 온다, 2026-10-11 실측). 그것이 없을 때만 검색 전체 상태(run_search)로 판정한다
    found_status = found.context.status if found and found.context else None
    if found_status is not None:
        completed = found_status == 'complete'
    results = found.results if found else []
    destinations = []
    for result in results:
        content = result.content
        if result.type != 'LOCATION' or content is None or content.location is None or content.location.type != CITY_LOCATION_TYPE:
            continue
        quotes = content.flight_quotes
        quote = (quotes.direct if direct else quotes.cheapest) if quotes else None
        if quote is None or quote.raw_price is None:
            continue
        parent = content.location.parent_location
        destinations.append(Destination(
            code=content.location.sky_code or None,
            city=content.location.name,
            country=parent.name if parent else None,
            country_code=parent.sky_code if parent else None,
            min_price_krw=round(quote.raw_price),
            direct=quote.direct,
            round_trip=round_trip,
        ))
    destinations.sort(key=lambda destination: destination.min_price_krw or 0)
    notes = [
        ESTIMATE_NOTE,
        '목적지 code 는 스카이스캐너 도시 코드라 IATA 가 아닐 수 있습니다 (예: CJUA). 검색에는 city 이름이나 search airports 의 code 를 쓰세요',
        '스카이스캐너 어디든지는 날짜를 주지 않습니다 (departure_date·return_date 는 null). calendar 로 날짜를 고르세요',
    ]
    if not completed:
        notes.append(f'{stopped_early(limited)} 받은 데까지만 보여 줍니다. 더 싼 목적지가 빠졌을 수 있습니다')
    if len(destinations) > limit:
        notes.append(f'도시 {len(destinations)}곳 중 싼 순 {limit}곳만 보여 줍니다 (--limit 최대 {EXPLORE_MAX_LIMIT})')
    shown = destinations[:limit]
    return SkyscannerExploreResult(
        origin=origin.code,
        trip_type='왕복' if round_trip else '편도',
        month=month,
        returned=len(shown),
        destinations=shown,
        notes=notes,
    )
