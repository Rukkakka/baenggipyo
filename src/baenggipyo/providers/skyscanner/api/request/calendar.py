from pydantic import Field

from baenggipyo.core.schema import RequestModel
from baenggipyo.providers.skyscanner.api.request.common import (
    CURRENCY,
    LOCALE,
    MARKET,
)

from typing import Annotated


class SkyscannerCalendarHeadersModel(RequestModel):
    """달력 API 는 시장·통화·로캘을 HTTP 헤더가 아니라 본문의 `headers` 로 받는다."""

    x_skyscanner_client: Annotated[
        str,
        Field(description='클라이언트 이름 (사이트 홈은 banana)')
    ] = Field(default='banana', alias='xSkyscannerClient')
    x_skyscanner_currency: Annotated[
        str,
        Field(description='통화')
    ] = Field(default=CURRENCY, alias='xSkyscannerCurrency')
    x_skyscanner_locale: Annotated[
        str,
        Field(description='로캘')
    ] = Field(default=LOCALE, alias='xSkyscannerLocale')
    x_skyscanner_market: Annotated[
        str,
        Field(description='시장')
    ] = Field(default=MARKET, alias='xSkyscannerMarket')


class SkyscannerSearchIntentPricecalendarV1RequestPayloadModel(RequestModel):
    """`POST /g/search-intent/v1/pricecalendar` 본문. 편도 날짜별 최저가 366일치를 준다.

    장소는 스카이 ID 다: 공항은 IATA(`ICN`), 도시는 IATA 뒤에 A(`SELA`). 도시를 `SEL` 로 주면 500 이다 (2026-10-11 실측).
    """

    headers: Annotated[
        SkyscannerCalendarHeadersModel,
        Field(description='시장·통화·로캘')
    ] = Field(default_factory=SkyscannerCalendarHeadersModel)
    origin_relevant_flight_sky_id: Annotated[
        str,
        Field(pattern=r'^[A-Z0-9]{3,5}$', description='출발 스카이 ID')
    ] = Field(alias='originRelevantFlightSkyId')
    destination_relevant_flight_sky_id: Annotated[
        str,
        Field(pattern=r'^[A-Z0-9]{3,5}$', description='도착 스카이 ID')
    ] = Field(alias='destinationRelevantFlightSkyId')
