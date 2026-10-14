from pydantic import Field

from baenggipyo.core.schema import ResponseModel
from baenggipyo.providers.naver.api.response.common import (
    NaverGraphQLResponseModel,
    NaverOtaModel,
)

from typing import Annotated


# ---------------------------------------------------------------------------
# POST /flight/domestic/searchFlights — SSE 이벤트 하나
# ---------------------------------------------------------------------------

class NaverDomesticPointModel(ResponseModel):
    airport_code: Annotated[
        str,
        Field(description='공항 코드')
    ] = Field(alias='airportCode')
    date: Annotated[
        str | None,
        Field(description='현지 날짜 YYYYMMDD')
    ] = None
    time: Annotated[
        str | None,
        Field(description='현지 시각 HHmm')
    ] = None

    @property
    def local_date_time(self) -> str | None:
        return self.date + self.time if self.date and self.time else None


class NaverDomesticSegmentModel(ResponseModel):
    """국내선 편 (모두 직항이라 구간이 하나다)."""

    airline_code: Annotated[
        str,
        Field(description='항공사 코드')
    ] = Field(alias='airlineCode')
    op_code: Annotated[
        str | None,
        Field(description='운항 항공사 코드 (공동운항일 때만, 아니면 빈 문자열)')
    ] = Field(default=None, alias='opCode')
    flight_number: Annotated[
        str | None,
        Field(description='편명 숫자')
    ] = Field(default=None, alias='flightNumber')
    departure: Annotated[
        NaverDomesticPointModel,
        Field(description='출발')
    ]
    arrival: Annotated[
        NaverDomesticPointModel,
        Field(description='도착')
    ]
    flight_duration: Annotated[
        int | None,
        Field(description='비행 시간 (분)')
    ] = Field(default=None, alias='flightDuration')


class NaverDomesticCardTypeModel(ResponseModel):
    code: Annotated[
        str | None,
        Field(description='카드 코드 (B86 등)')
    ] = None
    name: Annotated[
        str | None,
        Field(description='카드 이름')
    ] = None


class NaverDomesticDiscountInfoModel(ResponseModel):
    card_type: Annotated[
        NaverDomesticCardTypeModel | None,
        Field(description='할인 카드')
    ] = Field(default=None, alias='cardType')
    discount_type: Annotated[
        str | None,
        Field(description='할인 종류 (C = 카드)')
    ] = Field(default=None, alias='discountType')


class NaverDomesticDiscountFareModel(ResponseModel):
    """카드 할인 운임. 할인 카드로 결제할 때만 받는다."""

    adult_discount_fare: Annotated[
        int | None,
        Field(description='성인 1인 할인 총액 (원)')
    ] = Field(default=None, alias='adultDiscountFare')
    discount_info: Annotated[
        NaverDomesticDiscountInfoModel | None,
        Field(description='할인 카드·종류')
    ] = Field(default=None, alias='discountInfo')


class NaverDomesticFareModel(ResponseModel):
    """판매처 하나의 운임. 1인 총액은 운임 + 유류할증료 + 세금 + 발권 수수료다 (사이트 계산식)."""

    agt_code: Annotated[
        str,
        Field(description='판매처 코드')
    ] = Field(alias='agtCode')
    adult_fare: Annotated[
        int,
        Field(description='성인 운임')
    ] = Field(alias='adultFare')
    child_fare: Annotated[
        int,
        Field(description='소아 운임 (0 이면 판매처가 소아 운임을 주지 않음)')
    ] = Field(default=0, alias='childFare')
    a_fuel: Annotated[
        int,
        Field(description='성인 유류할증료')
    ] = Field(default=0, alias='aFuel')
    c_fuel: Annotated[
        int,
        Field(description='소아 유류할증료')
    ] = Field(default=0, alias='cFuel')
    a_tax: Annotated[
        int,
        Field(description='성인 세금')
    ] = Field(default=0, alias='aTax')
    c_tax: Annotated[
        int,
        Field(description='소아 세금')
    ] = Field(default=0, alias='cTax')
    publish_fee: Annotated[
        int | None,
        Field(description='발권 수수료')
    ] = Field(default=None, alias='publishFee')
    support_n_pay: Annotated[
        bool,
        Field(description='네이버페이 지원')
    ] = Field(default=False, alias='supportNPay')
    booking_class: Annotated[
        str,
        Field(description='예약 클래스')
    ] = Field(alias='bookingClass')
    etc: Annotated[
        str,
        Field(description='판매처 고유 값 (판매처 조회에 그대로 넘긴다)')
    ] = ''
    discount_fare: Annotated[
        NaverDomesticDiscountFareModel | None,
        Field(description='카드 할인 운임 (있을 때만)')
    ] = Field(default=None, alias='discountFare')


class NaverDomesticFlightModel(ResponseModel):
    """국내선 편 하나 (가는 편 또는 오는 편)."""

    itinerary_id: Annotated[
        str,
        Field(description='편 ID (예: 202611151910GMPCJUWE6503)')
    ] = Field(alias='itineraryId')
    type: Annotated[
        str,
        Field(description='departure (가는 편) 또는 arrival (오는 편)')
    ]
    seat_class: Annotated[
        str,
        Field(description='좌석 등급 (NR·NB·L 특가석, D 할인석, Y 일반석, C 비즈니스석)')
    ] = Field(alias='seatClass')
    seat_count: Annotated[
        int | None,
        Field(description='남은 좌석')
    ] = Field(default=None, alias='seatCount')
    fares: Annotated[
        list[NaverDomesticFareModel],
        Field(description='판매처별 운임')
    ] = []
    min_fare: Annotated[
        int | None,
        Field(description='판매처 최저 1인 총액')
    ] = Field(default=None, alias='minFare')
    duration: Annotated[
        int | None,
        Field(description='소요 시간 (분)')
    ] = None
    segment: Annotated[
        NaverDomesticSegmentModel,
        Field(description='편')
    ]


class NaverDomesticStatusModel(ResponseModel):
    """국내선 검색 상태와 코드표."""

    flight_search_key: Annotated[
        str | None,
        Field(description='서버 캐시 키 (예: YC|A:1/C:0/I:0|20261115GMPCJU)')
    ] = Field(default=None, alias='flightSearchKey')
    version: Annotated[
        int | None,
        Field(description='캐시 조회에 넘기는 결과 버전')
    ] = Field(default=None, alias='__v')
    available_partner_count: Annotated[
        int | None,
        Field(description='조회를 맡긴 판매처 수')
    ] = Field(default=None, alias='availablePartnerCount')
    complete_partner_count: Annotated[
        int | None,
        Field(description='응답을 마친 판매처 수')
    ] = Field(default=None, alias='completePartnerCount')
    is_complete: Annotated[
        bool | None,
        Field(description='모든 판매처 응답이 끝났는지')
    ] = Field(default=None, alias='isComplete')
    airlines_code_map: Annotated[
        dict[str, str],
        Field(description='항공사 코드 → 이름')
    ] = Field(default={}, alias='airlinesCodeMap')
    op_code_map: Annotated[
        dict[str, str],
        Field(description='운항 항공사 코드 → 이름')
    ] = Field(default={}, alias='opCodeMap')


class NaverFlightDomesticSearchFlightsResponseModel(ResponseModel):
    """`/flight/domestic/searchFlights` SSE 이벤트 하나."""

    is_expired: Annotated[
        bool | None,
        Field(description='캐시 조회했는데 캐시가 없거나 만료됐는지')
    ] = Field(default=None, alias='isExpired')
    status: Annotated[
        NaverDomesticStatusModel | None,
        Field(description='진행 상태와 코드표')
    ] = None
    flights: Annotated[
        list[NaverDomesticFlightModel],
        Field(description='편 목록 (filter.type 의 방향만)')
    ] = []


# ---------------------------------------------------------------------------
# POST /graphql — bookingOptions (searchType: DOMESTIC)
# ---------------------------------------------------------------------------

class NaverDomesticBookingOptionsModel(ResponseModel):
    combined: Annotated[
        list[NaverOtaModel],
        Field(description='가는 편·오는 편을 서로 다른 판매처에서 사는 조합')
    ] = []
    ota_list: Annotated[
        list[NaverOtaModel],
        Field(description='한 판매처에서 사는 운임')
    ] = Field(default=[], alias='otaList')


class NaverBookingOptionsDomesticDataModel(ResponseModel):
    booking_options: Annotated[
        NaverDomesticBookingOptionsModel | None,
        Field(description='판매처 목록')
    ] = Field(default=None, alias='bookingOptions')


class NaverBookingOptionsDomesticResponseModel(NaverGraphQLResponseModel):
    """GraphQL `bookingOptions`(국내선) 응답."""

    data: Annotated[
        NaverBookingOptionsDomesticDataModel | None,
        Field(description='결과. 에러면 null')
    ] = None
