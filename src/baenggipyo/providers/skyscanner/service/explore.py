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
    SkyscannerPlace,
    run_search,
    stopped_early,
)

from typing import Annotated


EXPLORE_DEFAULT_LIMIT: int = 20
EXPLORE_MAX_LIMIT: int = 100
CITY_LOCATION_TYPE: str = 'City'


class SkyscannerDestination(Destination):
    """스카이스캐너 목적지. IATA 코드를 주지 않아 `code` 는 null 이고, 스카이스캐너 도시 코드를 `sky_code` 로 따로 준다.

    도시 코드(CJUA·CYNT·JHSG)는 IATA 와 꼴이 비슷해 `code` 에 두면 IATA 로 읽혀 검색에 그대로 들어간다. 스카이스캐너 'CJUA'(제주)
    자동완성은 청주·청두를 돌려줘 엉뚱한 곳을 권하게 된다 (2026-10-20 실측). 규칙으로 IATA 를 만들지도 않는다 (CYNT 는 옌타이 YNT).
    """

    sky_code: Annotated[
        str | None,
        Field(description='스카이스캐너 도시 코드 (예: CJUA). IATA 가 아니라 검색에 쓸 수 없다. 모르면 null')
    ] = None


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
        list[SkyscannerDestination],
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
    response, completed, stopped = run_search(client, payload)
    found = response.everywhere_destination
    # 어디든지 결과의 상태는 결과 안의 context 다 (바로 complete 로 온다, 2026-10-11 실측). 그것이 없을 때만 검색 전체 상태(run_search)로 판정한다
    found_status = found.context.status if found and found.context else None
    if found_status is not None:
        completed = found_status == 'complete'
    results = found.results if found else []
    destinations = []
    # 지표 가격이 없어(--direct 면 직항 지표 가격이 없어) 뺀 도시 수. 광고·나라 항목과 달리 목적지라 notes 로 알린다
    unpriced = 0
    for result in results:
        content = result.content
        if result.type != 'LOCATION' or content is None or content.location is None or content.location.type != CITY_LOCATION_TYPE:
            continue
        quotes = content.flight_quotes
        quote = (quotes.direct if direct else quotes.cheapest) if quotes else None
        if quote is None or quote.raw_price is None:
            unpriced += 1
            continue
        # 나라는 location 밖(content.parentLocation)에 온다. location 안에 오던 때의 응답도 읽는다 (2026-10-20 실측: 도시 100곳 모두 location 안은 null)
        parent = content.parent_location or content.location.parent_location
        destinations.append(SkyscannerDestination(
            code=None,
            sky_code=content.location.sky_code or None,
            city=content.location.name,
            country=parent.name if parent else None,
            country_code=parent.sky_code if parent else None,
            min_price_krw=round(quote.raw_price),
            direct=quote.direct,
            round_trip=round_trip,
        ))
    destinations.sort(key=lambda destination: destination.min_price_krw or 0)
    # 결과마다 붙는 안내라 하나로 짧게 둔다 (references/output-contract.md '항공권 출력'). sky_code 를 검색에 넣으면 요청 없이
    # AIRPORT_NOT_FOUND 와 hint 가 가니 따로 알리지 않는다. 지표 가격은 그 달 어느 날의 최저가다. 인천→제주 26,928원은 11/9·11/23 같은 날의
    # 값이고 11/15 는 315,865원이었다 (2026-10-20 실측 달력). 스카이스캐너 calendar 는 편도만 돼 왕복은 날짜를 찾는 명령이 없다
    find_dates = (
        'calendar 는 편도만 돼 왕복 날짜는 찾을 수 없으니 그 달 날짜로 search flights 해 확인하세요' if round_trip
        else 'calendar 로 그 가격의 날짜를 찾은 뒤 search flights 로 확인하세요'
    )
    notes = [f'이 가격은 실시간이 아닌 추정가이고, 날짜를 주지 않아 그 기간 어느 날의 최저가라 다른 날은 몇 배 비쌀 수 있습니다. {find_dates}']
    if not completed:
        notes.append(f'{stopped_early(stopped)} 받은 데까지만 보여 줍니다. 더 싼 목적지가 빠졌을 수 있습니다')
    if unpriced:
        notes.append(f"{'직항 ' if direct else ''}지표 가격이 없는 도시 {unpriced}곳은 뺐습니다")
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
