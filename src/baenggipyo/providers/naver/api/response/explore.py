from pydantic import Field

from baenggipyo.core.schema import ResponseModel
from baenggipyo.providers.naver.api.response.common import NaverGraphQLResponseModel

from typing import Annotated


class NaverExploreLocationModel(ResponseModel):
    airport_code: Annotated[
        str,
        Field(description='목적지 공항 코드', alias='airportCode')
    ]
    city_name: Annotated[
        str | None,
        Field(description='도시 이름', alias='cityName')
    ] = None
    is_domestic: Annotated[
        bool | None,
        Field(description='국내인지', alias='isDomestic')
    ] = None


class NaverMinPriceByDestinationModel(ResponseModel):
    """목적지 하나의 최저가. 달력처럼 최근 검색에서 모은 값이다."""

    destination: Annotated[
        NaverExploreLocationModel,
        Field(description='목적지')
    ]
    departure_date: Annotated[
        str | None,
        Field(description='최저가의 출발 날짜 (YYYYMMDD)', alias='departureDate')
    ] = None
    return_date: Annotated[
        str | None,
        Field(description='최저가의 오는 날짜 (왕복만)', alias='returnDate')
    ] = None
    country_code: Annotated[
        str | None,
        Field(description='나라 코드', alias='countryCode')
    ] = None
    min_price: Annotated[
        int | None,
        Field(description='최저가 (원)', alias='minPrice')
    ] = None
    stops: Annotated[
        int | None,
        Field(description='경유 횟수')
    ] = None
    trip_days: Annotated[
        int | None,
        Field(description='여행 일수 (왕복만)', alias='tripDays')
    ] = None


class NaverMinPricesByDestinationDataModel(ResponseModel):
    min_prices_by_destination: Annotated[
        list[NaverMinPriceByDestinationModel] | None,
        Field(description='목적지별 최저가 (인기순)', alias='minPricesByDestination')
    ] = None


class NaverMinPricesByDestinationResponseModel(NaverGraphQLResponseModel):
    """GraphQL `minPricesByDestination` 응답."""

    data: Annotated[
        NaverMinPricesByDestinationDataModel | None,
        Field(description='결과. 에러면 null')
    ] = None
