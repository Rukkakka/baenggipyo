from pydantic import Field

from baenggipyo.core.schema import RequestModel

from typing import Annotated


# 사이트 번들의 autocompleteLocations 문서에서 쓰는 필드만 남겼다 (인트로스펙션이 막혀 있어 번들 쿼리를 기준으로 한다)
AUTOCOMPLETE_LOCATIONS_QUERY: str = """
query autocompleteLocations($airportOnly: Boolean!, $query: String!) {
  autocompleteLocations(airportOnly: $airportOnly, query: $query) {
    __typename
    ... on SearchAirport {
      type
      airportCode
      airportName
      cityCode
      cityName
      countryCode
      countryName
      isDomestic
    }
    ... on SearchCity {
      type
      cityCode
      cityName
      airports
      countryCode
      countryName
      isDomestic
    }
  }
}
"""


class NaverAutocompleteLocationsRequestPayloadModel(RequestModel):
    """GraphQL `autocompleteLocations` 변수."""

    query: Annotated[
        str,
        Field(min_length=1, max_length=50, description='공항·도시 이름 또는 코드')
    ]
    airport_only: Annotated[
        bool,
        Field(description='공항만 찾을지 (도시 제외)')
    ] = Field(default=False, alias='airportOnly')
