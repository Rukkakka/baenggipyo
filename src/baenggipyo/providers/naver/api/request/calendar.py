from pydantic import Field

from baenggipyo.core.schema import RequestModel
from baenggipyo.providers.naver.api.request.common import (
    DATE_DIGITS_PATTERN,
    LOCATION_CODE_PATTERN,
    NaverLocationType,
    NaverTripCode,
)

from typing import Annotated


MIN_PRICES_BY_DATE_QUERY: str = """
query minPricesByDate($departureLocationCode: String, $departureLocationType: String, $arrivalLocationCode: String, $arrivalLocationType: String, $departureDate: String, $groupByDepartureDate: Boolean, $isNonstop: Boolean, $tripDays: [Int!], $tripType: String) {
  minPricesByDate(departureLocationCode: $departureLocationCode, departureLocationType: $departureLocationType, arrivalLocationCode: $arrivalLocationCode, arrivalLocationType: $arrivalLocationType, departureDate: $departureDate, groupByDepartureDate: $groupByDepartureDate, isNonstop: $isNonstop, tripDays: $tripDays, tripType: $tripType) {
    departureDate
    returnDate
    minPrice
    tripType
  }
}
"""


class NaverMinPricesByDateRequestPayloadModel(RequestModel):
    """GraphQL `minPricesByDate` 변수.

    편도는 출발일 없이 약 1년치를 준다. 왕복은 `group_by_departure_date=True` 면 출발일마다 왕복 최저가를,
    `departure_date` 를 주면 그날 출발의 오는 날 11일치를 준다 (2026-10-11 실측).
    """

    departure_location_code: Annotated[
        str,
        Field(pattern=LOCATION_CODE_PATTERN, description='출발 IATA 코드')
    ] = Field(alias='departureLocationCode')
    departure_location_type: Annotated[
        NaverLocationType,
        Field(description='출발지 종류')
    ] = Field(alias='departureLocationType')
    arrival_location_code: Annotated[
        str,
        Field(pattern=LOCATION_CODE_PATTERN, description='도착 IATA 코드')
    ] = Field(alias='arrivalLocationCode')
    arrival_location_type: Annotated[
        NaverLocationType,
        Field(description='도착지 종류')
    ] = Field(alias='arrivalLocationType')
    trip_type: Annotated[
        NaverTripCode,
        Field(description='OW 또는 RT')
    ] = Field(alias='tripType')
    departure_date: Annotated[
        str | None,
        Field(pattern=DATE_DIGITS_PATTERN, description='왕복에서 출발일을 정할 때 (YYYYMMDD)')
    ] = Field(default=None, alias='departureDate')
    group_by_departure_date: Annotated[
        bool,
        Field(description='왕복을 출발일마다 묶을지')
    ] = Field(default=False, alias='groupByDepartureDate')
    is_nonstop: Annotated[
        bool | None,
        Field(description='직항만')
    ] = Field(default=None, alias='isNonstop')
    trip_days: Annotated[
        list[int] | None,
        Field(description='여행 일수 [최소, 최대]. 가는 날과 오는 날을 모두 센다 (2박 3일 = 3)')
    ] = Field(default=None, alias='tripDays')
