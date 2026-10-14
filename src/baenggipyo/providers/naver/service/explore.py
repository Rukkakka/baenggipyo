"""어디든지: 출발지만 정하고 목적지별 최저가를 찾는다 (`explore`)."""

from pydantic import Field

from baenggipyo.core.flight import (
    Destination,
    FlightModel,
    TripType,
    compact_date,
)
from baenggipyo.core.schema import built_from_response
from baenggipyo.providers.naver.api.client import NaverClient
from baenggipyo.providers.naver.api.request.explore import (
    NaverMinPricesByDestinationRequestPayloadModel,
)
from baenggipyo.providers.naver.service.common import (
    ESTIMATE_NOTE,
    unwrap_graphql,
)

from typing import Annotated


# 서버는 인기순으로 준다. 가격순으로 세우려고 넉넉히 받은 뒤 자른다
EXPLORE_FETCH_SIZE: int = 100
EXPLORE_DEFAULT_LIMIT: int = 20
EXPLORE_MAX_LIMIT: int = EXPLORE_FETCH_SIZE


class NaverExploreResult(FlightModel):
    """`explore` 결과. 편도에는 여행 일수가 없어 `trip_days` 키를 뺀다 (해당 없음)."""

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
        Field(description='출발 달 (YYYY-MM). 주지 않았으면 null')
    ] = None
    trip_days: Annotated[
        int | None,
        Field(description='왕복 여행 일수. 왕복일 때만 키가 있다')
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

    def not_applicable_fields(self) -> frozenset[str]:
        return frozenset({'trip_days'}) if self.trip_type == '편도' else frozenset()


def explore(
    client: NaverClient,
    origin_code: str,
    *,
    month: str | None = None,
    trip_days: int | None = None,
    direct: bool = False,
    limit: int = EXPLORE_DEFAULT_LIMIT,
) -> NaverExploreResult:
    """국제선 목적지별 최저가를 싼 순으로 (요청 1회).

    국내선 목적지는 1차 범위 밖이라 `isDomestic: false` 로 묻는다. 서버는 인기순으로 주므로 `EXPLORE_FETCH_SIZE` 개를 받아
    가격순으로 세운 뒤 자른다.

    Args:
        origin_code: 출발 IATA 공항·도시 코드.
        month: `YYYY-MM`. None 이면 서버 기본 범위.
        trip_days: 왕복 여행 일수 (가는 날·오는 날 포함). None 이면 편도.
    """
    round_trip = trip_days is not None
    # 출발·도착 코드는 자동완성 응답 값이라 검증 실패는 입력 오류가 아니다 (달·일수는 CLI 가 검사했다)
    with built_from_response('네이버 어디든지'):
        payload = NaverMinPricesByDestinationRequestPayloadModel(
            departure_location_code=origin_code,
            trip_type='RT' if round_trip else 'OW',
            departure_months=[month.replace('-', '')] if month else None,
            trip_days=[trip_days, trip_days] if trip_days is not None else None,
            is_nonstop=True if direct else None,
            is_domestic=False,
            size=EXPLORE_FETCH_SIZE,
        )
    response = client.run_request_min_prices_by_destination(payload)
    data = unwrap_graphql(response, response.data, '어디든지')
    rows = sorted(
        (row for row in data.min_prices_by_destination or [] if row.min_price is not None),
        key=lambda row: row.min_price or 0,
    )
    destinations = [
        Destination(
            code=row.destination.airport_code,
            city=row.destination.city_name,
            country_code=row.country_code,
            min_price_krw=row.min_price,
            direct=row.stops == 0 if row.stops is not None else None,
            departure_date=compact_date(row.departure_date),
            return_date=compact_date(row.return_date),
            round_trip=round_trip,
        )
        for row in rows[:limit]
    ]
    notes = [ESTIMATE_NOTE, '나라 이름은 주지 않습니다 (country 는 null, country_code 를 보세요)']
    if len(rows) > limit:
        notes.append(f'목적지 {len(rows)}곳 중 싼 순 {limit}곳만 보여 줍니다 (--limit 최대 {EXPLORE_MAX_LIMIT})')
    return NaverExploreResult(
        origin=origin_code,
        trip_type='왕복' if round_trip else '편도',
        month=month,
        trip_days=trip_days,
        returned=len(destinations),
        destinations=destinations,
        notes=notes,
    )
