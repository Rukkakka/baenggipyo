"""두 기능 이상이 쓰는 요청 모델: 검색 API(`radar/api/v2/web-unified-search`)는 항공권 검색과 어디든지가 같이 쓴다."""

from pydantic import Field

from baenggipyo.core.schema import RequestModel

from typing import (
    Annotated,
    Literal,
)


SkyscannerCabinClass = Literal['ECONOMY', 'PREMIUM_ECONOMY', 'BUSINESS', 'FIRST']

MARKET: str = 'KR'
LOCALE: str = 'ko-KR'
CURRENCY: str = 'KRW'
# 스카이스캐너 요청 모델의 승객 유형별 상한. CLI 의 합계 상한(cli/output.py)·다른 소스와 값만 같고 뜻이 달라 일부러 따로 둔다
MAX_PASSENGERS: int = 9


class SkyscannerPlaceModel(RequestModel):
    """구간의 출발·도착. 장소는 엔티티 ID(`GeoId`)로, 어디든지는 `{"@type": "everywhere"}` 로 보낸다."""

    type: Annotated[
        Literal['entity', 'everywhere'],
        Field(description='entity 또는 everywhere', alias='@type')
    ]
    entity_id: Annotated[
        str | None,
        Field(pattern=r'^\d+$', description='엔티티 ID (예: 인천 95673659)', alias='entityId')
    ] = None


class SkyscannerDateModel(RequestModel):
    """구간의 날짜. 어디든지는 달(`month`)이나 언제든(`anytime`)으로 보낸다."""

    type: Annotated[
        Literal['date', 'month', 'anytime'],
        Field(description='date, month, anytime', alias='@type')
    ]
    year: Annotated[
        str | None,
        Field(pattern=r'^\d{4}$', description='연도')
    ] = None
    month: Annotated[
        str | None,
        Field(pattern=r'^\d{2}$', description='월 (두 자리)')
    ] = None
    day: Annotated[
        str | None,
        Field(pattern=r'^\d{2}$', description='일 (두 자리, date 만)')
    ] = None


class SkyscannerSearchLegModel(RequestModel):
    """구간 하나."""

    leg_origin: Annotated[
        SkyscannerPlaceModel,
        Field(description='출발', alias='legOrigin')
    ]
    leg_destination: Annotated[
        SkyscannerPlaceModel,
        Field(description='도착', alias='legDestination')
    ]
    dates: Annotated[
        SkyscannerDateModel,
        Field(description='날짜')
    ]


class SkyscannerRadarWebUnifiedSearchV2RequestPayloadModel(RequestModel):
    """`POST /g/radar/api/v2/web-unified-search/` 본문 (사이트 FlightsDayView 번들과 같은 모양)."""

    cabin_class: Annotated[
        SkyscannerCabinClass,
        Field(description='좌석 등급', alias='cabinClass')
    ] = 'ECONOMY'
    child_ages: Annotated[
        list[int],
        Field(description='소아·유아 나이 (유아는 0~1)', alias='childAges')
    ] = []
    adults: Annotated[
        int,
        Field(ge=1, le=MAX_PASSENGERS, description='성인 수')
    ] = 1
    legs: Annotated[
        list[SkyscannerSearchLegModel],
        Field(min_length=1, description='구간 (편도 1, 왕복 2, 다구간 여럿)')
    ]
    target_geo_level: Annotated[
        Literal['city'] | None,
        Field(description='어디든지 결과 단위. city 면 도시, 없으면 나라', alias='targetGeoLevel')
    ] = None


class SkyscannerRadarWebUnifiedSearchPollV2RequestParameterModel(RequestModel):
    """`GET /g/radar/api/v2/web-unified-search/{sessionId}` 경로 파라미터."""

    session_id: Annotated[
        str,
        Field(min_length=1, exclude=True, description='검색 POST 응답의 context.sessionId', alias='sessionId')
    ]
