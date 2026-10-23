"""두 기능 이상이 쓰는 요청 값 타입."""

from pydantic import Field

from baenggipyo.core.schema import RequestModel

from typing import (
    Annotated,
    Any,
    Literal,
)


NaverTripCode = Literal['OW', 'RT', 'MD']
NaverSeatClass = Literal['Y', 'P', 'C', 'F']
NaverLocationType = Literal['airport', 'city']

LOCATION_CODE_PATTERN: str = r'^[A-Z]{3}$'
DATE_DIGITS_PATTERN: str = r'^\d{8}$'
# 네이버 요청 모델의 승객 유형별 상한. CLI 의 합계 상한(cli/output.py)·다른 소스와 값만 같고 뜻이 달라 일부러 따로 둔다
MAX_PASSENGERS: int = 9

BOOKING_PARTNERS_QUERY: str = """
query bookingPartners {
  bookingPartners {
    otaId
    expName
  }
}
"""


class NaverBookingItineraryModel(RequestModel):
    """판매처 조회의 여정 한 구간 (`searchParams.itinerary[]`)."""

    departure_airport: Annotated[
        str,
        Field(description='출발 공항 코드', alias='departureAirport')
    ]
    arrival_airport: Annotated[
        str,
        Field(description='도착 공항 코드', alias='arrivalAirport')
    ]
    departure_date: Annotated[
        str,
        Field(pattern=DATE_DIGITS_PATTERN, description='출발 날짜 (YYYYMMDD)', alias='departureDate')
    ]


class NaverBookingPersonModel(RequestModel):
    """승객 수 (`searchParams.person`)."""

    adult: Annotated[
        int,
        Field(ge=1, description='성인 수')
    ]
    child: Annotated[
        int,
        Field(ge=0, description='소아 수')
    ] = 0
    infant: Annotated[
        int,
        Field(ge=0, description='유아 수')
    ] = 0


class NaverBookingPartnersRequestPayloadModel(RequestModel):
    """GraphQL `bookingPartners` 변수 (없음)."""


class NaverFlightFilterModel(RequestModel):
    """서버 필터·정렬·페이지 (`flightFilter`)."""

    filter: Annotated[
        dict[str, Any],
        Field(description='서버 필터 (airlines, selectedItineraries 등)')
    ] = {}
    limit: Annotated[
        int,
        Field(ge=1, description='받을 운임 묶음 수. 응답 크기에 직접 영향을 준다')
    ] = 20
    skip: Annotated[
        int,
        Field(ge=0, description='건너뛸 운임 묶음 수')
    ] = 0
    sort: Annotated[
        dict[str, int],
        Field(description='정렬 {키: 1 오름차순 | -1 내림차순}. 비우면 가격 낮은 순')
    ] = {}
