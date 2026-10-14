from pydantic import Field

from baenggipyo.core.schema import RequestModel
from baenggipyo.providers.naver.api.request.common import (
    LOCATION_CODE_PATTERN,
    NaverTripCode,
)

from typing import Annotated


MIN_PRICES_BY_DESTINATION_QUERY: str = """
query minPricesByDestination($departureLocationCode: String, $departureMonths: [String!], $isDomestic: Boolean, $isNonstop: Boolean, $size: Int, $tripDays: [Int!], $tripType: String) {
  minPricesByDestination(departureLocationCode: $departureLocationCode, departureMonths: $departureMonths, isDomestic: $isDomestic, isNonstop: $isNonstop, size: $size, tripDays: $tripDays, tripType: $tripType) {
    destination {
      airportCode
      cityName
      isDomestic
    }
    departureDate
    returnDate
    countryCode
    minPrice
    stops
    tripDays
    tripType
  }
}
"""


class NaverMinPricesByDestinationRequestPayloadModel(RequestModel):
    """GraphQL `minPricesByDestination`(어디든지) 변수. 결과는 가격순이 아니라 인기순이다 (2026-10-11 실측)."""

    departure_location_code: Annotated[
        str,
        Field(pattern=LOCATION_CODE_PATTERN, description='출발 IATA 공항·도시 코드')
    ] = Field(alias='departureLocationCode')
    trip_type: Annotated[
        NaverTripCode,
        Field(description='OW 또는 RT')
    ] = Field(alias='tripType')
    departure_months: Annotated[
        list[str] | None,
        Field(description='출발 달 (YYYYMM)')
    ] = Field(default=None, alias='departureMonths')
    trip_days: Annotated[
        list[int] | None,
        Field(description='여행 일수 [최소, 최대] (왕복). 가는 날과 오는 날을 모두 센다')
    ] = Field(default=None, alias='tripDays')
    is_nonstop: Annotated[
        bool | None,
        Field(description='직항만')
    ] = Field(default=None, alias='isNonstop')
    is_domestic: Annotated[
        bool | None,
        Field(description='국내선만(True)·국제선만(False)')
    ] = Field(default=None, alias='isDomestic')
    size: Annotated[
        int | None,
        Field(ge=1, description='목적지 수')
    ] = None
