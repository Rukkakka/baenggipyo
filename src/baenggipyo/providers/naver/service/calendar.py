"""날짜별 최저가 (`calendar`)."""

from pydantic import Field

from baenggipyo.core.flight import (
    CalendarDay,
    CheapestDays,
    FlightModel,
    TripType,
    cheapest_days,
    compact_date,
)
from baenggipyo.core.schema import built_from_response
from baenggipyo.providers.naver.api.client import NaverClient
from baenggipyo.providers.naver.api.request.calendar import (
    NaverMinPricesByDateRequestPayloadModel,
)
from baenggipyo.providers.naver.service.common import (
    ESTIMATE_NOTE,
    NaverLocation,
    unwrap_graphql,
)

from typing import Annotated


class NaverCalendarResult(FlightModel):
    """`calendar` 결과. 편도에는 여행 일수가 없어 `trip_days` 키를 뺀다 (해당 없음)."""

    origin: Annotated[
        str,
        Field(description='출발 코드')
    ]
    destination: Annotated[
        str,
        Field(description='도착 코드')
    ]
    trip_type: Annotated[
        TripType,
        Field(description='편도 또는 왕복')
    ]
    trip_days: Annotated[
        int | None,
        Field(description='왕복 여행 일수 (가는 날·오는 날 포함). 왕복일 때만 키가 있다')
    ] = None
    month: Annotated[
        str,
        Field(description='조회한 달 (YYYY-MM)')
    ]
    direct: Annotated[
        bool,
        Field(description='직항만 찾았는지 (--direct). 날마다의 직항 여부는 네이버가 주지 않는다')
    ]
    returned: Annotated[
        int,
        Field(description='값이 있는 날 수')
    ]
    cheapest_days: CheapestDays
    days: Annotated[
        list[CalendarDay],
        Field(description='날짜별 최저가 (값이 있는 날만, 날짜 순)')
    ]
    notes: Annotated[
        list[str],
        Field(exclude=True, description='결과 안내')
    ] = []

    def not_applicable_fields(self) -> frozenset[str]:
        return frozenset({'trip_days'}) if self.trip_type == '편도' else frozenset()


def get_calendar(
    client: NaverClient,
    origin: NaverLocation,
    destination: NaverLocation,
    *,
    month: str,
    trip_days: int | None = None,
    direct: bool = False,
) -> NaverCalendarResult:
    """한 달의 날짜별 최저가 (요청 1회).

    편도는 약 1년치를 받아 그 달만 남긴다. `trip_days` 를 주면 왕복이고, 출발일마다 그 일수의 왕복 최저가를 받는다
    (`groupByDepartureDate`, 2026-10-11 실측: tripDays [4, 4] → 10/12~10/15).

    Args:
        month: `YYYY-MM`.
        trip_days: 왕복 여행 일수 (가는 날과 오는 날 포함, 2박 3일 = 3). None 이면 편도.
        direct: 직항만.
    """
    round_trip = trip_days is not None
    # 출발·도착 코드는 자동완성 응답 값이라 검증 실패는 입력 오류가 아니다 (달·일수는 CLI 가 검사했다)
    with built_from_response('네이버 날짜별 최저가'):
        payload = NaverMinPricesByDateRequestPayloadModel(
            departure_location_code=origin.code,
            departure_location_type=origin.type,
            arrival_location_code=destination.code,
            arrival_location_type=destination.type,
            trip_type='RT' if round_trip else 'OW',
            group_by_departure_date=round_trip,
            is_nonstop=True if direct else None,
            trip_days=[trip_days, trip_days] if trip_days is not None else None,
        )
    response = client.run_request_min_prices_by_date(payload)
    data = unwrap_graphql(response, response.data, '날짜별 최저가')
    days = []
    for row in data.min_prices_by_date or []:
        date = compact_date(row.departure_date)
        if date is None or not date.startswith(month):
            continue
        days.append(CalendarDay(
            date=date,
            return_date=compact_date(row.return_date),
            min_price_krw=row.min_price,
            round_trip=round_trip,
        ))
    days.sort(key=lambda day: day.date)
    notes = [ESTIMATE_NOTE]
    if not days:
        notes.append(f'{month} 에 값이 있는 날이 없습니다. 최근에 이 노선을 검색한 사람이 적으면 비어 있을 수 있으니 search flights 로 찾으세요')
    return NaverCalendarResult(
        origin=origin.code,
        destination=destination.code,
        trip_type='왕복' if round_trip else '편도',
        trip_days=trip_days,
        month=month,
        direct=direct,
        returned=len(days),
        cheapest_days=cheapest_days(days),
        days=days,
        notes=notes,
    )
