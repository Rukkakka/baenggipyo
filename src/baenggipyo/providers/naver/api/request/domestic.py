from pydantic import Field

from baenggipyo.core.schema import RequestModel
from baenggipyo.providers.naver.api.request.common import (
    DATE_DIGITS_PATTERN,
    LOCATION_CODE_PATTERN,
    NaverBookingItineraryModel,
    NaverBookingPersonModel,
    NaverFlightFilterModel,
    NaverTripCode,
)

from typing import (
    Annotated,
    Literal,
)


# 국내선 좌석 조건 (번들 zod 스키마). YC 전체, Y 할인·일반·특가석, C 비즈니스석. 서버 캐시 키는 늘 YC 라(실측)
# 좌석은 flightFilter.filter.seatClass 로 건다
NaverDomesticFareType = Literal['YC', 'C', 'Y', 'D', 'L', 'NR', 'NB']

# getOtaList 와 같은 bookingOptions 지만 searchType: DOMESTIC 이고, 판매처가 다른 왕복 조합(combined)도 준다
BOOKING_OPTIONS_DOMESTIC_QUERY: str = """
query bookingOptions($combinedFares: [OtaFareInput!], $device: DeviceType!, $isFareLoadCompleted: Boolean, $otaFares: [OtaFareInput!]!, $scheduleIds: [String!]!, $searchParams: SearchParamsInput!) {
  bookingOptions(input: {combinedFares: $combinedFares, device: $device, isFareLoadCompleted: $isFareLoadCompleted, otaFares: $otaFares, scheduleIds: $scheduleIds, searchParams: $searchParams, searchType: DOMESTIC}) {
    combined {
      active(searchType: DOMESTIC) {
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
    otaList {
      active(searchType: DOMESTIC) {
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


class NaverDomesticItineraryModel(RequestModel):
    """국내선 검색 구간 하나. 국내선은 공항 코드만 받는다."""

    departure_airport: Annotated[
        str,
        Field(pattern=LOCATION_CODE_PATTERN, description='출발 공항 코드', alias='departureAirport')
    ]
    arrival_airport: Annotated[
        str,
        Field(pattern=LOCATION_CODE_PATTERN, description='도착 공항 코드', alias='arrivalAirport')
    ]
    departure_date: Annotated[
        str,
        Field(pattern=DATE_DIGITS_PATTERN, description='출발 날짜 (YYYYMMDD)', alias='departureDate')
    ]


class NaverFlightDomesticSearchFlightsRequestPayloadModel(RequestModel):
    """`POST /flight/domestic/searchFlights` 본문 (번들 zod 스키마와 같은 필드).

    왕복은 가는 편(`flight_filter.filter.type = departure`)과 오는 편(`arrival`) 목록을 따로 받는다.
    """

    type: Annotated[
        Literal['domestic'],
        Field(description='늘 domestic')
    ] = 'domestic'
    device: Annotated[
        Literal['pc', 'mobile'],
        Field(description='기기')
    ] = 'pc'
    fare_type: Annotated[
        NaverDomesticFareType,
        Field(description='좌석 조건. 서버 캐시 키는 늘 YC 라 YC 로 보내고 좌석은 필터로 건다', alias='fareType')
    ] = 'YC'
    itineraries: Annotated[
        list[NaverDomesticItineraryModel],
        Field(min_length=1, max_length=2, description='구간 (편도 1, 왕복 2)')
    ]
    person: Annotated[
        NaverBookingPersonModel,
        Field(description='승객 수')
    ]
    trip_type: Annotated[
        NaverTripCode,
        Field(description='OW 또는 RT', alias='tripType')
    ]
    initial_request: Annotated[
        bool,
        Field(description='새 검색이면 True, 서버 캐시 조회면 False', alias='initialRequest')
    ] = True
    version: Annotated[
        int | None,
        Field(description='캐시 조회에 쓰는 결과 버전 (status.__v)')
    ] = None
    flight_filter: Annotated[
        NaverFlightFilterModel,
        Field(description='서버 필터 (type, seatClass, segment__airlineCode …)·정렬·페이지', alias='flightFilter')
    ] = Field(default_factory=NaverFlightFilterModel)


class NaverDomesticBookingParamsModel(RequestModel):
    """판매처 조회에 넘기는 국내선 편·운임 (번들 함수 그대로: 편 정보와 운임 필드를 옮긴다)."""

    airline_code: Annotated[
        str,
        Field(description='항공사 코드', alias='airlineCode')
    ]
    fit_name: Annotated[
        str,
        Field(description='편명 숫자', alias='fitName')
    ]
    dep_city: Annotated[
        str,
        Field(description='출발 공항', alias='depCity')
    ]
    arr_city: Annotated[
        str,
        Field(description='도착 공항', alias='arrCity')
    ]
    departure_date_time: Annotated[
        str,
        Field(description='출발 YYYYMMDDHHmm', alias='departureDateTime')
    ]
    arrival_date_time: Annotated[
        str,
        Field(description='도착 YYYYMMDDHHmm', alias='arrivalDateTime')
    ]
    seat_class: Annotated[
        str,
        Field(description='예약 클래스 (운임의 bookingClass)', alias='seatClass')
    ]
    etc: Annotated[
        str,
        Field(description='판매처 고유 값 (운임의 etc 그대로)')
    ] = ''
    seat_cnt: Annotated[
        int | None,
        Field(description='남은 좌석', alias='seatCnt')
    ] = None
    adult_fare: Annotated[
        int,
        Field(description='성인 운임 + 세금 + 유류할증료', alias='adultFare')
    ]
    child_fare: Annotated[
        int,
        Field(description='소아 운임 + 세금 + 유류할증료', alias='childFare')
    ]
    a_fuel: Annotated[
        int,
        Field(description='성인 유류할증료', alias='aFuel')
    ]
    c_fuel: Annotated[
        int,
        Field(description='소아 유류할증료', alias='cFuel')
    ]
    a_tax: Annotated[
        int,
        Field(description='성인 세금', alias='aTax')
    ]
    c_tax: Annotated[
        int,
        Field(description='소아 세금', alias='cTax')
    ]
    publish_fee: Annotated[
        int | None,
        Field(description='발권 수수료', alias='publishFee')
    ] = None


class NaverDomesticOtaFareModel(RequestModel):
    """국내선 판매처 운임 하나 (`OtaFareInput`)."""

    ota_id: Annotated[
        str,
        Field(description='판매처 코드 (agtCode)', alias='otaId')
    ]
    support_n_pay: Annotated[
        bool,
        Field(description='네이버페이 지원', alias='supportNPay')
    ] = False
    fare: Annotated[
        int,
        Field(ge=0, description='모든 승객 합계 (원)')
    ]
    orig_fare: Annotated[
        int,
        Field(ge=0, description='할인 전 합계 (원)', alias='origFare')
    ]
    fare_type: Annotated[
        str,
        Field(description='A01 (모든 결제수단)', alias='fareType')
    ] = 'A01'
    booking_params: Annotated[
        list[NaverDomesticBookingParamsModel],
        Field(min_length=1, description='편마다 하나 (왕복이면 둘)', alias='bookingParams')
    ]


class NaverDomesticBookingSearchParamsModel(RequestModel):
    """국내선 판매처 조회의 검색 조건."""

    itinerary: Annotated[
        list[NaverBookingItineraryModel],
        Field(min_length=1, description='고른 편의 구간')
    ]
    person: Annotated[
        NaverBookingPersonModel,
        Field(description='승객 수')
    ]
    trip: Annotated[
        NaverTripCode,
        Field(description='OW 또는 RT')
    ]
    fare_type: Annotated[
        NaverDomesticFareType,
        Field(description='좌석 조건 (검색과 같은 YC)', alias='fareType')
    ] = 'YC'


class NaverBookingOptionsDomesticRequestPayloadModel(RequestModel):
    """GraphQL `bookingOptions`(searchType: DOMESTIC) 변수."""

    search_params: Annotated[
        NaverDomesticBookingSearchParamsModel,
        Field(description='검색 조건', alias='searchParams')
    ]
    ota_fares: Annotated[
        list[NaverDomesticOtaFareModel],
        Field(description='판매처마다 고른 편 전부의 운임', alias='otaFares')
    ]
    combined_fares: Annotated[
        list[NaverDomesticOtaFareModel],
        Field(description='왕복에서 가는 편·오는 편 최저가 판매처가 다르면 그 둘', alias='combinedFares')
    ] = []
    device: Annotated[
        Literal['PC', 'MOBILE'],
        Field(description='DeviceType enum')
    ] = 'PC'
    schedule_ids: Annotated[
        list[str],
        Field(min_length=1, description='편마다 항공사-편명-출발YYYYMMDDHHmm-좌석등급', alias='scheduleIds')
    ]
    is_fare_load_completed: Annotated[
        bool,
        Field(description='운임을 다 받았는지', alias='isFareLoadCompleted')
    ] = True
