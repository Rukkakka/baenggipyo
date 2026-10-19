"""날짜별 최저가 (`calendar`)."""

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from baenggipyo.core.flight import (
    CalendarDay,
    TripType,
)
from baenggipyo.core.schema import built_from_response
from baenggipyo.providers.skyscanner.api.client import SkyscannerClient
from baenggipyo.providers.skyscanner.api.request.calendar import (
    SkyscannerSearchIntentPricecalendarV1RequestPayloadModel,
)
from baenggipyo.providers.skyscanner.service.common import (
    ESTIMATE_NOTE,
    SkyscannerPlace,
)

from typing import Annotated


class SkyscannerCalendarResult(BaseModel):
    """`calendar` 결과 (편도만)."""

    model_config = ConfigDict(
        extra='forbid',
    )

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
        Field(description='편도 (스카이스캐너 달력은 편도만)')
    ] = '편도'
    month: Annotated[
        str,
        Field(description='조회한 달 (YYYY-MM)')
    ]
    returned: Annotated[
        int,
        Field(description='값이 있는 날 수')
    ]
    days: Annotated[
        list[CalendarDay],
        Field(description='날짜별 추정 최저가 (값이 있는 날만)')
    ]
    notes: Annotated[
        list[str],
        Field(exclude=True, description='결과 안내')
    ] = []


def get_calendar(client: SkyscannerClient, origin: SkyscannerPlace, destination: SkyscannerPlace, *, month: str) -> SkyscannerCalendarResult:
    """한 달의 편도 날짜별 추정 최저가 (요청 1회. 366일치를 받아 그 달만 남긴다)."""
    # 스카이 ID 는 자동완성 응답 값이라 검증 실패는 입력 오류가 아니다
    with built_from_response('스카이스캐너 날짜별 최저가'):
        payload = SkyscannerSearchIntentPricecalendarV1RequestPayloadModel(
            origin_relevant_flight_sky_id=origin.sky_id,
            destination_relevant_flight_sky_id=destination.sky_id,
        )
    response = client.run_request_search_intent_pricecalendar_v1(payload)
    days = [
        CalendarDay(date=day.day, min_price_krw=round(day.price) if day.price is not None else None)
        for day in (response.flights.days if response.flights else [])
        if day.day.startswith(month) and day.price is not None
    ]
    notes = [ESTIMATE_NOTE]
    if not days:
        notes.append(f'{month} 에 값이 있는 날이 없습니다. search flights 로 찾으세요')
    return SkyscannerCalendarResult(origin=origin.code, destination=destination.code, month=month, returned=len(days), days=days, notes=notes)
