from pydantic import Field

from baenggipyo.core.schema import ResponseModel
from baenggipyo.providers.naver.api.response.common import NaverGraphQLResponseModel

from typing import Annotated


class NaverLocationModel(ResponseModel):
    """자동완성 후보 하나. `__typename` 이 `SearchAirport` 면 공항, `SearchCity` 면 도시다."""

    typename: Annotated[
        str,
        Field(description='SearchAirport, SearchCity, SearchCountry, SearchContinent')
    ] = Field(alias='__typename')
    airport_code: Annotated[
        str | None,
        Field(description='공항 코드 (공항만)')
    ] = Field(default=None, alias='airportCode')
    airport_name: Annotated[
        str | None,
        Field(description='공항 이름 (공항만)')
    ] = Field(default=None, alias='airportName')
    city_code: Annotated[
        str | None,
        Field(description='도시 코드')
    ] = Field(default=None, alias='cityCode')
    city_name: Annotated[
        str | None,
        Field(description='도시 이름')
    ] = Field(default=None, alias='cityName')
    airports: Annotated[
        list[str] | None,
        Field(description='도시에 속한 공항 코드 (도시만)')
    ] = None
    country_code: Annotated[
        str | None,
        Field(description='나라 코드')
    ] = Field(default=None, alias='countryCode')
    country_name: Annotated[
        str | None,
        Field(description='나라 이름')
    ] = Field(default=None, alias='countryName')
    is_domestic: Annotated[
        bool | None,
        Field(description='한국 국내인지')
    ] = Field(default=None, alias='isDomestic')


class NaverAutocompleteLocationsDataModel(ResponseModel):
    autocomplete_locations: Annotated[
        list[NaverLocationModel] | None,
        Field(description='후보 (도시가 그 도시의 공항보다 먼저 온다)')
    ] = Field(default=None, alias='autocompleteLocations')


class NaverAutocompleteLocationsResponseModel(NaverGraphQLResponseModel):
    """GraphQL `autocompleteLocations` 응답."""

    data: Annotated[
        NaverAutocompleteLocationsDataModel | None,
        Field(description='결과. 에러면 null')
    ] = None
