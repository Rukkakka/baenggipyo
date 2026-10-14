from pydantic import (
    Field,
    RootModel,
)

from baenggipyo.core.schema import ResponseModel

from typing import Annotated


class SkyscannerAutosuggestPlaceModel(ResponseModel):
    """자동완성 후보 하나. `PlaceId == CityId` 면 도시, 아니면 공항이다 (나라는 `PlaceId == CountryId`).

    `CityId` 가 빈 도시 후보도 있다 ('부산' 의 PUSA, 2026-10-12 실측). 도시는 자기 엔티티가 곧 컨테이너라 `GeoId == GeoContainerId` 다.
    """

    place_id: Annotated[
        str,
        Field(description='스카이 ID (공항 NRT, 도시 TYOA, 나라 JP)')
    ] = Field(alias='PlaceId')
    place_name: Annotated[
        str | None,
        Field(description='이름 (공항은 "도쿄 나리타" 처럼 도시 이름이 붙는다)')
    ] = Field(default=None, alias='PlaceName')
    iata_code: Annotated[
        str | None,
        Field(description='도시 IATA 코드 (도시만 채워진다)')
    ] = Field(default=None, alias='IataCode')
    city_id: Annotated[
        str | None,
        Field(description='도시 스카이 ID')
    ] = Field(default=None, alias='CityId')
    city_name: Annotated[
        str | None,
        Field(description='도시 이름')
    ] = Field(default=None, alias='CityName')
    country_id: Annotated[
        str | None,
        Field(description='나라 코드')
    ] = Field(default=None, alias='CountryId')
    country_name: Annotated[
        str | None,
        Field(description='나라 이름')
    ] = Field(default=None, alias='CountryName')
    geo_id: Annotated[
        str | None,
        Field(description='엔티티 ID (검색에 쓴다)')
    ] = Field(default=None, alias='GeoId')
    geo_container_id: Annotated[
        str | None,
        Field(description='속한 도시의 엔티티 ID (도시는 자기 GeoId)')
    ] = Field(default=None, alias='GeoContainerId')


class SkyscannerAutosuggestSearchSearchFlightV1ResponseModel(RootModel[list[SkyscannerAutosuggestPlaceModel]]):
    """자동완성 응답 (최상위가 배열)."""
