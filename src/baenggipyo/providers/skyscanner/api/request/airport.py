from pydantic import Field

from baenggipyo.core.schema import RequestModel
from baenggipyo.providers.skyscanner.api.request.common import (
    LOCALE,
    MARKET,
)

from typing import Annotated


class SkyscannerAutosuggestSearchSearchFlightV1RequestParameterModel(RequestModel):
    """`GET /g/autosuggest-search/api/v1/search-flight/{market}/{locale}/{query}` 파라미터."""

    market: Annotated[
        str,
        Field(exclude=True, description='시장 (경로)')
    ] = MARKET
    locale: Annotated[
        str,
        Field(exclude=True, description='로캘 (경로)')
    ] = LOCALE
    query: Annotated[
        str,
        Field(min_length=1, max_length=256, exclude=True, description='검색어 (경로, 256자까지)')
    ]
    is_destination: Annotated[
        bool,
        Field(description='도착지로 찾는지', alias='isDestination')
    ] = False
    enable_general_search_v2: Annotated[
        bool,
        Field(description='사이트가 늘 true 로 보낸다')
    ] = True
