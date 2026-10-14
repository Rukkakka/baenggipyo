from pydantic import Field

from baenggipyo.core.schema import ResponseModel
from baenggipyo.providers.naver.api.response.common import (
    NaverGraphQLResponseModel,
    NaverOtaModel,
)

from typing import Annotated


class NaverFareTypeModel(ResponseModel):
    """운임 종류 코드의 뜻 (`status.fareTypesCodeMap` 의 값)."""

    name: Annotated[
        str | None,
        Field(description='예: 성인/모든 결제수단, 성인/현대카드')
    ] = None
    category: Annotated[
        str | None,
        Field(description='예: 신용카드(즉시할인), 간편결제. 모든 결제수단(A01)은 빈 문자열')
    ] = None


class NaverAirportNameModel(ResponseModel):
    """`status.airportsCodeMap` 의 값."""

    airport_name: Annotated[
        str | None,
        Field(description='공항 이름')
    ] = Field(default=None, alias='airportName')
    city_name: Annotated[
        str | None,
        Field(description='도시 이름')
    ] = Field(default=None, alias='cityName')


class NaverSearchStatusModel(ResponseModel):
    """검색 진행 상태와 코드표."""

    search_key: Annotated[
        str | None,
        Field(description='검색 조건에서 만든 서버 캐시 키 (예: OWY100PN20261115ICNANRTA)')
    ] = Field(default=None, alias='searchKey')
    version: Annotated[
        int | None,
        Field(description='캐시 조회(initialRequest=false)에 넘기는 결과 버전')
    ] = Field(default=None, alias='__v')
    requested_partner_count: Annotated[
        int | None,
        Field(description='조회를 맡긴 판매처 수')
    ] = Field(default=None, alias='requestedPartnerCount')
    completed_partner_count: Annotated[
        int | None,
        Field(description='응답을 마친 판매처 수')
    ] = Field(default=None, alias='completedPartnerCount')
    is_completed: Annotated[
        bool | None,
        Field(description='모든 판매처 응답이 끝났는지')
    ] = Field(default=None, alias='isCompleted')
    expire_at: Annotated[
        str | None,
        Field(description='서버 캐시 만료 시각 (UTC, 검색 뒤 약 15분)')
    ] = Field(default=None, alias='expireAt')
    airlines_code_map: Annotated[
        dict[str, str],
        Field(description='항공사 코드 → 한국어 이름')
    ] = Field(default={}, alias='airlinesCodeMap')
    airports_code_map: Annotated[
        dict[str, NaverAirportNameModel],
        Field(description='공항 코드 → 이름')
    ] = Field(default={}, alias='airportsCodeMap')
    fare_types_code_map: Annotated[
        dict[str, NaverFareTypeModel],
        Field(description='운임 종류 코드 → 이름·분류')
    ] = Field(default={}, alias='fareTypesCodeMap')


class NaverPointModel(ResponseModel):
    """구간의 출발·도착 지점."""

    airport_code: Annotated[
        str,
        Field(description='공항 코드')
    ] = Field(alias='airportCode')
    date_time: Annotated[
        str | None,
        Field(description='현지 시각 YYYYMMDDHHmm. 일부 여정은 이 키 없이 date·time 만 준다 (2026-10-11 실측)')
    ] = Field(default=None, alias='dateTime')
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
        """현지 시각 YYYYMMDDHHmm. `dateTime` 이 없으면 `date` + `time` 으로 만든다."""
        if self.date_time:
            return self.date_time
        if self.date and self.time:
            return self.date + self.time
        return None


class NaverCarrierModel(ResponseModel):
    """판매·운항 항공사."""

    airline_code: Annotated[
        str,
        Field(description='항공사 IATA 코드')
    ] = Field(alias='airlineCode')
    flight_number: Annotated[
        str | None,
        Field(description='편명 숫자 (앞에 0 이 붙는다, 예: 0605)')
    ] = Field(default=None, alias='flightNumber')


class NaverSegmentModel(ResponseModel):
    """비행 한 번."""

    departure: Annotated[
        NaverPointModel,
        Field(description='출발')
    ]
    arrival: Annotated[
        NaverPointModel,
        Field(description='도착')
    ]
    marketing_carrier: Annotated[
        NaverCarrierModel,
        Field(description='판매(표기) 항공사')
    ] = Field(alias='marketingCarrier')
    operating_carrier: Annotated[
        NaverCarrierModel | None,
        Field(description='운항 항공사')
    ] = Field(default=None, alias='operatingCarrier')
    flight_duration: Annotated[
        int | None,
        Field(description='비행 시간 (초)')
    ] = Field(default=None, alias='flightDuration')
    aircraft_code: Annotated[
        str | None,
        Field(description='기종 (예: Airbus A321)')
    ] = Field(default=None, alias='aircraftCode')


class NaverItineraryModel(ResponseModel):
    """한 방향 여정."""

    itinerary_id: Annotated[
        str,
        Field(description='여정 ID (예: 20261115ICNNRTZE0605, 경유는 + 로 이음)')
    ] = Field(alias='itineraryId')
    sequence: Annotated[
        int | None,
        Field(description='1 = 가는 편, 2 = 오는 편 …')
    ] = None
    duration: Annotated[
        int | None,
        Field(description='경유 대기를 포함한 소요 시간 (초)')
    ] = None
    segments: Annotated[
        list[NaverSegmentModel],
        Field(description='비행 구간')
    ] = []


class NaverPassengerFareModel(ResponseModel):
    """승객 유형 하나의 1인 운임."""

    total_fare: Annotated[
        int | None,
        Field(description='1인 총액 (세금·유류할증료 포함, 원)')
    ] = Field(default=None, alias='totalFare')


class NaverFareModel(ResponseModel):
    """판매처 × 운임 종류 하나."""

    partner_code: Annotated[
        str,
        Field(description='판매처 코드 (예: TOV025)')
    ] = Field(alias='partnerCode')
    fare_type: Annotated[
        str,
        Field(description='운임 종류 (A01 모든 결제수단, A01/Bxx 카드·간편결제 할인, A07 장애인 …)')
    ] = Field(alias='fareType')
    adult: Annotated[
        NaverPassengerFareModel | None,
        Field(description='성인 1인 운임')
    ] = None
    child: Annotated[
        NaverPassengerFareModel | None,
        Field(description='소아 1인 운임')
    ] = None
    infant: Annotated[
        NaverPassengerFareModel | None,
        Field(description='유아 1인 운임')
    ] = None
    reservation_url: Annotated[
        str | None,
        Field(description='판매처 예약 URL. 새 검색에서는 늘 빈 문자열이고 캐시 조회(selectedItineraries)에서 채워진다')
    ] = Field(default=None, alias='reservationUrl')
    baggage_fee_type: Annotated[
        str | None,
        Field(description='FREE(수하물 포함)·CHARGE(유료)·UNKNOWN')
    ] = Field(default=None, alias='baggageFeeType')


class NaverFareMappingModel(ResponseModel):
    """항공권 하나(여정 조합)의 운임 목록."""

    itinerary_ids: Annotated[
        str,
        Field(description='여정 ID 를 - 로 이은 항공권 ID')
    ] = Field(alias='itineraryIds')
    fares: Annotated[
        list[NaverFareModel],
        Field(description='판매처 × 운임 종류')
    ] = []
    curation: Annotated[
        list[str],
        Field(description='라벨 (MIN_PRICE, NONSTOP_MIN_PRICE …)')
    ] = []


class NaverFlightInternationalSearchFlightsResponseModel(ResponseModel):
    """`searchFlights` SSE 이벤트 하나. 이벤트마다 그때까지의 결과 전체(누적)가 온다."""

    unique_id: Annotated[
        str | None,
        Field(description='응답 ID')
    ] = Field(default=None, alias='uniqueId')
    is_expired: Annotated[
        bool | None,
        Field(description='캐시 조회했는데 캐시가 없거나 만료됐는지')
    ] = Field(default=None, alias='isExpired')
    status: Annotated[
        NaverSearchStatusModel | None,
        Field(description='진행 상태와 코드표')
    ] = None
    itineraries: Annotated[
        list[NaverItineraryModel],
        Field(description='여정 목록 (limit 와 상관없이 전부 온다)')
    ] = []
    fare_mappings: Annotated[
        list[NaverFareMappingModel],
        Field(description='항공권별 운임 (flightFilter 의 정렬·limit 를 따른다)')
    ] = Field(default=[], alias='fareMappings')


# ---------------------------------------------------------------------------
# POST /graphql — getOtaList (bookingOptions), bookingPartners
# ---------------------------------------------------------------------------


class NaverBookingOptionsModel(ResponseModel):
    ota_list: Annotated[
        list[NaverOtaModel],
        Field(description='판매처별 운임')
    ] = Field(default=[], alias='otaList')


class NaverBookingOptionsDataModel(ResponseModel):
    booking_options: Annotated[
        NaverBookingOptionsModel | None,
        Field(description='판매처 목록')
    ] = Field(default=None, alias='bookingOptions')


class NaverBookingOptionsResponseModel(NaverGraphQLResponseModel):
    """GraphQL `getOtaList` 응답."""

    data: Annotated[
        NaverBookingOptionsDataModel | None,
        Field(description='결과. 에러면 null')
    ] = None
