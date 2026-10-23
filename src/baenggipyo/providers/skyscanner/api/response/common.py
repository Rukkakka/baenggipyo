"""두 기능 이상이 쓰는 응답 모델: 검색 API 응답 (항공권 검색과 어디든지)."""

from pydantic import Field

from baenggipyo.core.schema import ResponseModel

from typing import Annotated


class SkyscannerSearchContextModel(ResponseModel):
    """검색 상태. `incomplete` 면 `session_id` 로 다시 GET 한다."""

    status: Annotated[
        str | None,
        Field(description='incomplete 또는 complete')
    ] = None
    session_id: Annotated[
        str | None,
        Field(description='폴링에 쓰는 세션 ID', alias='sessionId')
    ] = None
    total_results: Annotated[
        int | None,
        Field(description='결과 수', alias='totalResults')
    ] = None


class SkyscannerSegmentPlaceModel(ResponseModel):
    """구간의 공항."""

    display_code: Annotated[
        str | None,
        Field(description='IATA 공항 코드', alias='displayCode')
    ] = None
    flight_place_id: Annotated[
        str | None,
        Field(description='스카이 ID', alias='flightPlaceId')
    ] = None
    name: Annotated[
        str | None,
        Field(description='공항 이름')
    ] = None


class SkyscannerCarrierModel(ResponseModel):
    """항공사."""

    name: Annotated[
        str | None,
        Field(description='항공사 이름')
    ] = None
    display_code: Annotated[
        str | None,
        Field(description='IATA 항공사 코드 (예: RS)', alias='displayCode')
    ] = None
    alternate_id: Annotated[
        str | None,
        Field(description='스카이스캐너 내부 코드 (예: S~)', alias='alternateId')
    ] = None


class SkyscannerSegmentModel(ResponseModel):
    """비행 한 번."""

    id: Annotated[
        str | None,
        Field(description='구간 ID (판매처 예약 단위의 segmentIds 가 가리킨다)')
    ] = None
    origin: Annotated[
        SkyscannerSegmentPlaceModel,
        Field(description='출발 공항')
    ]
    destination: Annotated[
        SkyscannerSegmentPlaceModel,
        Field(description='도착 공항')
    ]
    departure: Annotated[
        str | None,
        Field(description='현지 시각 YYYY-MM-DDTHH:MM:SS')
    ] = None
    arrival: Annotated[
        str | None,
        Field(description='현지 시각 YYYY-MM-DDTHH:MM:SS')
    ] = None
    duration_in_minutes: Annotated[
        int | None,
        Field(description='비행 시간 (분)', alias='durationInMinutes')
    ] = None
    flight_number: Annotated[
        str | None,
        Field(description='편명 숫자 (항공사 코드 없이)', alias='flightNumber')
    ] = None
    marketing_carrier: Annotated[
        SkyscannerCarrierModel | None,
        Field(description='판매(표기) 항공사', alias='marketingCarrier')
    ] = None
    operating_carrier: Annotated[
        SkyscannerCarrierModel | None,
        Field(description='운항 항공사', alias='operatingCarrier')
    ] = None


class SkyscannerLegModel(ResponseModel):
    """한 방향 여정."""

    duration_in_minutes: Annotated[
        int | None,
        Field(description='경유 대기를 포함한 소요 시간 (분)', alias='durationInMinutes')
    ] = None
    stop_count: Annotated[
        int | None,
        Field(description='경유 횟수', alias='stopCount')
    ] = None
    departure: Annotated[
        str | None,
        Field(description='현지 출발 시각')
    ] = None
    arrival: Annotated[
        str | None,
        Field(description='현지 도착 시각')
    ] = None
    segments: Annotated[
        list[SkyscannerSegmentModel],
        Field(description='비행 구간')
    ] = []


class SkyscannerOptionPriceModel(ResponseModel):
    amount: Annotated[
        float | None,
        Field(description='모든 승객 합계 (요청 통화)')
    ] = None
    update_status: Annotated[
        str | None,
        Field(description='current 등', alias='updateStatus')
    ] = None


class SkyscannerPricingItemModel(ResponseModel):
    """판매처 운임의 예약 단위. 여러 개면 판매처 여러 곳에서 따로 예약한다.

    부산↔후쿠오카 왕복 운임 3,375개 중 6개가 예약 단위 2개였다: 가는 편은 이스타항공, 오는 편은 티웨이처럼 단위마다 판매처·가격·구간·링크가
    따로 있고, 단위 가격의 합이 운임 가격이다. 예약 단위가 하나면 그 가격이 운임 가격과 같다 (3,369개 모두, 2026-10-11).
    """

    agent_id: Annotated[
        str | None,
        Field(description='판매처 ID', alias='agentId')
    ] = None
    url: Annotated[
        str | None,
        Field(description='예약 딥링크 (사이트 기준 상대 경로 /transport_deeplink/...)')
    ] = None
    price: Annotated[
        SkyscannerOptionPriceModel | None,
        Field(description='이 예약 단위의 가격 (모든 승객 합계)')
    ] = None
    segment_ids: Annotated[
        list[str],
        Field(description='이 예약 단위로 사는 구간 ID (SkyscannerSegmentModel.id)', alias='segmentIds')
    ] = []


class SkyscannerPricingOptionModel(ResponseModel):
    """판매처 하나의 운임."""

    pricing_option_id: Annotated[
        str | None,
        Field(description='운임 ID', alias='pricingOptionId')
    ] = None
    agent_ids: Annotated[
        list[str],
        Field(description='판매처 ID', alias='agentIds')
    ] = []
    price: Annotated[
        SkyscannerOptionPriceModel | None,
        Field(description='가격')
    ] = None
    items: Annotated[
        list[SkyscannerPricingItemModel],
        Field(description='예약 단위')
    ] = []
    unpriced_type: Annotated[
        str | None,
        Field(
            description='판매처가 가격을 주지 않은 운임 (itinerary_not_returned·partner_not_selected). 이때 가격은 0 이다 '
            '(ICN→LHR 31개·부산↔후쿠오카 175개 모두, 2026-10-11)',
            alias='unpricedType'
        )
    ] = None


class SkyscannerItineraryPriceModel(ResponseModel):
    raw: Annotated[
        float | None,
        Field(description='대표 가격 (모든 승객 합계)')
    ] = None
    pricing_option_id: Annotated[
        str | None,
        Field(description='대표 가격의 운임 ID', alias='pricingOptionId')
    ] = None


class SkyscannerItineraryModel(ResponseModel):
    """항공권 한 건."""

    id: Annotated[
        str,
        Field(description='항공권 ID (장소·시각·항공사로 만든 값이라 같은 조건으로 다시 검색해도 같다)')
    ]
    price: Annotated[
        SkyscannerItineraryPriceModel | None,
        Field(description='대표 가격')
    ] = None
    legs: Annotated[
        list[SkyscannerLegModel],
        Field(description='여정')
    ] = []
    pricing_options: Annotated[
        list[SkyscannerPricingOptionModel],
        Field(description='판매처별 운임', alias='pricingOptions')
    ] = []
    is_self_transfer: Annotated[
        bool | None,
        Field(description='직접 갈아타는(셀프 환승) 항공권인지', alias='isSelfTransfer')
    ] = None


class SkyscannerAgentModel(ResponseModel):
    """판매처."""

    id: Annotated[
        str,
        Field(description='판매처 ID')
    ]
    name: Annotated[
        str | None,
        Field(description='판매처 이름')
    ] = None
    is_carrier: Annotated[
        bool | None,
        Field(description='항공사 직판인지', alias='isCarrier')
    ] = None


class SkyscannerItinerariesModel(ResponseModel):
    results: Annotated[
        list[SkyscannerItineraryModel],
        Field(description='항공권')
    ] = []
    agents: Annotated[
        list[SkyscannerAgentModel],
        Field(description='판매처 목록')
    ] = []


class SkyscannerQuoteModel(ResponseModel):
    raw_price: Annotated[
        float | None,
        Field(description='지표 가격 (원)', alias='rawPrice')
    ] = None
    direct: Annotated[
        bool | None,
        Field(description='직항인지')
    ] = None


class SkyscannerFlightQuotesModel(ResponseModel):
    cheapest: Annotated[
        SkyscannerQuoteModel | None,
        Field(description='최저가')
    ] = None
    direct: Annotated[
        SkyscannerQuoteModel | None,
        Field(description='직항 최저가')
    ] = None


class SkyscannerLocationModel(ResponseModel):
    id: Annotated[
        str | None,
        Field(description='위치 ID')
    ] = None
    sky_code: Annotated[
        str | None,
        Field(description='스카이 코드 (도시 CJUA, 나라 KR)', alias='skyCode')
    ] = None
    name: Annotated[
        str | None,
        Field(description='이름')
    ] = None
    type: Annotated[
        str | None,
        Field(description='City, Nation …')
    ] = None
    parent_location: Annotated[
        'SkyscannerLocationModel | None',
        Field(description='상위 위치 (도시의 나라). 어디든지 결과는 2026-10-20 실측 늘 null 이고 content.parentLocation 에 온다', alias='parentLocation')
    ] = None


class SkyscannerEverywhereContentModel(ResponseModel):
    location: Annotated[
        SkyscannerLocationModel | None,
        Field(description='목적지')
    ] = None
    parent_location: Annotated[
        SkyscannerLocationModel | None,
        Field(description='목적지 도시의 나라 (skyCode 는 ISO 3166-1 alpha-2, 예 KR). 2026-10-20 실측 location 밖 여기로 온다', alias='parentLocation')
    ] = None
    flight_quotes: Annotated[
        SkyscannerFlightQuotesModel | None,
        Field(description='지표 가격', alias='flightQuotes')
    ] = None


class SkyscannerEverywhereResultModel(ResponseModel):
    """어디든지 결과 한 건. 광고(`ADVERT`)가 섞여 온다."""

    type: Annotated[
        str | None,
        Field(description='LOCATION 또는 ADVERT')
    ] = None
    content: Annotated[
        SkyscannerEverywhereContentModel | None,
        Field(description='내용')
    ] = None


class SkyscannerEverywhereDestinationModel(ResponseModel):
    context: Annotated[
        SkyscannerSearchContextModel | None,
        Field(description='상태 (바로 complete)')
    ] = None
    results: Annotated[
        list[SkyscannerEverywhereResultModel],
        Field(description='목적지·광고')
    ] = []


class SkyscannerRadarWebUnifiedSearchV2ResponseModel(ResponseModel):
    """검색 POST·폴링 GET 응답."""

    context: Annotated[
        SkyscannerSearchContextModel | None,
        Field(description='검색 상태')
    ] = None
    itineraries: Annotated[
        SkyscannerItinerariesModel | None,
        Field(description='항공권 검색 결과')
    ] = None
    everywhere_destination: Annotated[
        SkyscannerEverywhereDestinationModel | None,
        Field(description='어디든지 결과', alias='everywhereDestination')
    ] = None


class SkyscannerRadarWebUnifiedSearchPollV2ResponseModel(SkyscannerRadarWebUnifiedSearchV2ResponseModel):
    """폴링 GET 응답 (POST 와 같은 모양)."""
