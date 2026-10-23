from pydantic import (
    Field,
    ValidationInfo,
    field_validator,
)

from baenggipyo.core.schema import RequestModel
from baenggipyo.providers.naver.api.request.common import (
    DATE_DIGITS_PATTERN,
    LOCATION_CODE_PATTERN,
    MAX_PASSENGERS,
    NaverBookingItineraryModel,
    NaverBookingPersonModel,
    NaverFlightFilterModel,
    NaverLocationType,
    NaverSeatClass,
    NaverTripCode,
)

from typing import (
    Annotated,
    Literal,
)


# getOtaList(bookingOptions) 에서 쓰는 필드만 남겼다. 판매처 연락처·사업자 정보는 내보내지 않으므로 받지도 않는다
BOOKING_OPTIONS_QUERY: str = """
query getOtaList($curations: [String!]!, $device: DeviceType!, $isFareLoadCompleted: Boolean, $otaFares: [OtaFareInput!]!, $scheduleIds: [String!]!, $searchParams: SearchParamsInput!) {
  bookingOptions(input: {curations: $curations, device: $device, isFareLoadCompleted: $isFareLoadCompleted, otaFares: $otaFares, scheduleIds: $scheduleIds, searchParams: $searchParams, searchType: INTERNATIONAL}) {
    otaList {
      active(searchType: INTERNATIONAL) {
        realtime
      }
      bookingUrl
      expName
      fare
      fareType
      otaId
      otaType
      rank
    }
  }
}
"""


class NaverSearchItineraryModel(RequestModel):
    """검색할 여정 한 구간."""

    departure_location_code: Annotated[
        str,
        Field(pattern=LOCATION_CODE_PATTERN, description='출발 IATA 공항·도시 코드', alias='departureLocationCode')
    ]
    departure_location_type: Annotated[
        NaverLocationType,
        Field(description='출발지가 공항인지 도시인지', alias='departureLocationType')
    ]
    arrival_location_code: Annotated[
        str,
        Field(pattern=LOCATION_CODE_PATTERN, description='도착 IATA 공항·도시 코드', alias='arrivalLocationCode')
    ]
    arrival_location_type: Annotated[
        NaverLocationType,
        Field(description='도착지가 공항인지 도시인지', alias='arrivalLocationType')
    ]
    departure_date: Annotated[
        str,
        Field(pattern=DATE_DIGITS_PATTERN, description='출발 날짜 (YYYYMMDD)', alias='departureDate')
    ]


class NaverFlightInternationalSearchFlightsRequestPayloadModel(RequestModel):
    """`POST /flight/international/searchFlights` 본문 (사이트 번들의 zod 스키마와 같은 필드)."""

    trip_type: Annotated[
        NaverTripCode,
        Field(description='OW 편도, RT 왕복, MD 다구간', alias='tripType')
    ]
    device: Annotated[
        Literal['pc', 'mobile'],
        Field(description='기기')
    ] = 'pc'
    seat_class: Annotated[
        NaverSeatClass,
        Field(description='Y 일반석, P 프리미엄일반석, C 비즈니스석, F 일등석', alias='seatClass')
    ] = 'Y'
    adult_count: Annotated[
        int,
        Field(ge=1, le=MAX_PASSENGERS, description='성인 수', alias='adultCount')
    ] = 1
    child_count: Annotated[
        int,
        Field(ge=0, le=MAX_PASSENGERS, description='소아 수', alias='childCount')
    ] = 0
    infant_count: Annotated[
        int,
        Field(ge=0, le=MAX_PASSENGERS, description='유아 수', alias='infantCount')
    ] = 0
    is_nonstop: Annotated[
        bool,
        Field(description='직항만', alias='isNonstop')
    ] = False
    open_return_days: Annotated[
        int,
        Field(ge=0, description='사이트가 늘 0 으로 보낸다', alias='openReturnDays')
    ] = 0
    initial_request: Annotated[
        bool,
        Field(description='새 검색이면 True, 서버 캐시 조회면 False', alias='initialRequest')
    ] = True
    version: Annotated[
        int | None,
        Field(description='캐시 조회에 쓰는 검색 결과 버전 (status.__v)')
    ] = None
    itineraries: Annotated[
        list[NaverSearchItineraryModel],
        Field(min_length=1, description='여정 (편도 1, 왕복 2, 다구간 여럿)')
    ]
    flight_filter: Annotated[
        NaverFlightFilterModel,
        Field(description='서버 필터·정렬·페이지', alias='flightFilter')
    ] = Field(default_factory=NaverFlightFilterModel)

    @field_validator('infant_count')
    @classmethod
    def infants_need_adults(cls, value: int, info: ValidationInfo) -> int:
        adults = info.data.get('adult_count', 1)
        if value > adults:
            raise ValueError(f'유아는 성인 수({adults})보다 많을 수 없습니다')
        return value


class NaverBookingSearchParamsModel(RequestModel):
    """판매처 조회의 검색 조건 (`SearchParamsInput`)."""

    itinerary: Annotated[
        list[NaverBookingItineraryModel],
        Field(min_length=1, description='여정')
    ]
    person: Annotated[
        NaverBookingPersonModel,
        Field(description='승객 수')
    ]
    trip: Annotated[
        NaverTripCode,
        Field(description='OW·RT·MD')
    ]
    fare_type: Annotated[
        NaverSeatClass,
        Field(description='좌석 등급 코드', alias='fareType')
    ]


class NaverOtaFareModel(RequestModel):
    """판매처 조회에 넘기는 운임 하나 (`OtaFareInput`)."""

    ota_id: Annotated[
        str,
        Field(description='판매처 코드 (검색 운임의 partnerCode)', alias='otaId')
    ]
    fare: Annotated[
        int,
        Field(ge=0, description='모든 승객 합계 (원)')
    ]
    fare_type: Annotated[
        str,
        Field(description='운임 종류 코드 (A01, A01/B74 …)', alias='fareType')
    ]
    booking_url: Annotated[
        str,
        Field(description='검색 운임의 reservationUrl. 비어 있으면 서버가 판매처를 돌려주지 않는다', alias='bookingUrl')
    ]


class NaverBookingOptionsRequestPayloadModel(RequestModel):
    """GraphQL `getOtaList`(bookingOptions, 국제선) 변수."""

    search_params: Annotated[
        NaverBookingSearchParamsModel,
        Field(description='검색 조건', alias='searchParams')
    ]
    ota_fares: Annotated[
        list[NaverOtaFareModel],
        Field(description='항공권의 판매처별 운임', alias='otaFares')
    ]
    device: Annotated[
        Literal['PC', 'MOBILE'],
        Field(description='DeviceType enum (소문자 pc 는 BAD_USER_INPUT)')
    ] = 'PC'
    schedule_ids: Annotated[
        list[str],
        Field(min_length=1, description='여정 ID (가는 편, 오는 편 …)', alias='scheduleIds')
    ]
    curations: Annotated[
        list[str],
        Field(description='검색 결과의 curation 라벨')
    ] = []
    is_fare_load_completed: Annotated[
        bool,
        Field(description='운임을 다 받았는지', alias='isFareLoadCompleted')
    ] = True
