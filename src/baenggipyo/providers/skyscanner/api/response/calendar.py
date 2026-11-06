from pydantic import Field

from baenggipyo.core.schema import ResponseModel

from typing import Annotated


class SkyscannerCalendarDayModel(ResponseModel):
    """날짜 하나의 추정 최저가."""

    day: Annotated[
        str,
        Field(description='출발 날짜 YYYY-MM-DD')
    ]
    price: Annotated[
        float | None,
        Field(description='성인 1명 편도 추정 최저가 (원). 요청에 승객 항목이 없다 (2026-11-06 같은 날 검색과 비교)')
    ] = None
    group: Annotated[
        str | None,
        Field(description='low, medium, high')
    ] = None


class SkyscannerCalendarFlightsModel(ResponseModel):
    days: Annotated[
        list[SkyscannerCalendarDayModel],
        Field(description='오늘부터 366일')
    ] = []


class SkyscannerSearchIntentPricecalendarV1ResponseModel(ResponseModel):
    """`POST /g/search-intent/v1/pricecalendar` 응답."""

    flights: Annotated[
        SkyscannerCalendarFlightsModel | None,
        Field(description='편도 날짜별 추정가')
    ] = None
