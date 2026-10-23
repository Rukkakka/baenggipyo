from pydantic import Field

from baenggipyo.core.schema import ResponseModel
from baenggipyo.providers.naver.api.response.common import NaverGraphQLResponseModel

from typing import Annotated


class NaverMinPriceByDateModel(ResponseModel):
    """날짜 하나의 최저가. 최근 검색에서 모은 값이라 카드 할인가가 섞인다 (2026-10-11 실측)."""

    departure_date: Annotated[
        str,
        Field(description='출발 날짜 (YYYYMMDD)', alias='departureDate')
    ]
    return_date: Annotated[
        str | None,
        Field(description='오는 날짜 (왕복만, YYYYMMDD)', alias='returnDate')
    ] = None
    min_price: Annotated[
        int | None,
        Field(description='최저가 (원)', alias='minPrice')
    ] = None
    trip_type: Annotated[
        str | None,
        Field(description='OW 또는 RT', alias='tripType')
    ] = None


class NaverMinPricesByDateDataModel(ResponseModel):
    min_prices_by_date: Annotated[
        list[NaverMinPriceByDateModel] | None,
        Field(description='값이 있는 날짜만 온다', alias='minPricesByDate')
    ] = None


class NaverMinPricesByDateResponseModel(NaverGraphQLResponseModel):
    """GraphQL `minPricesByDate` 응답."""

    data: Annotated[
        NaverMinPricesByDateDataModel | None,
        Field(description='결과. 에러면 null')
    ] = None
