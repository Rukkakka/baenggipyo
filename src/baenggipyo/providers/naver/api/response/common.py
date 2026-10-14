"""두 기능 이상이 쓰는 응답 모델."""

from pydantic import Field

from baenggipyo.core.schema import ResponseModel

from typing import (
    Annotated,
    Any,
)


class NaverGraphQLErrorModel(ResponseModel):
    """GraphQL 에러 한 건. 잘못된 변수도 HTTP 200 에 `extensions.code: BAD_USER_INPUT` 으로 온다."""

    message: Annotated[
        str | None,
        Field(description='에러 메시지')
    ] = None
    extensions: Annotated[
        dict[str, Any] | None,
        Field(description='에러 코드 등 (code: BAD_USER_INPUT, GRAPHQL_VALIDATION_FAILED …)')
    ] = None


class NaverGraphQLResponseModel(ResponseModel):
    """GraphQL 응답 공통 봉투. 실패도 HTTP 200 에 `errors` 로 온다."""

    errors: Annotated[
        list[NaverGraphQLErrorModel],
        Field(description='에러. 없으면 []')
    ] = []


class NaverOtaActiveModel(ResponseModel):
    realtime: Annotated[
        bool | None,
        Field(description='실시간 예약 판매처로 보여 주는지 (사이트는 true 인 것만 판매처 목록에 보인다)')
    ] = None


class NaverOtaModel(ResponseModel):
    """판매처 하나의 운임."""

    ota_id: Annotated[
        str,
        Field(description='판매처 코드')
    ] = Field(alias='otaId')
    exp_name: Annotated[
        str | None,
        Field(description='판매처 이름')
    ] = Field(default=None, alias='expName')
    fare: Annotated[
        int | None,
        Field(description='넘긴 운임 (모든 승객 합계, 원)')
    ] = None
    fare_type: Annotated[
        str | None,
        Field(description='운임 종류 코드')
    ] = Field(default=None, alias='fareType')
    ota_type: Annotated[
        str | None,
        Field(description='KR_OTA, GLOBAL_OTA, KR_AIRLINE, GLOBAL_AIRLINE')
    ] = Field(default=None, alias='otaType')
    rank: Annotated[
        int | None,
        Field(description='사이트 표시 순위')
    ] = None
    active: Annotated[
        NaverOtaActiveModel | None,
        Field(description='노출 여부')
    ] = None
    booking_url: Annotated[
        str | None,
        Field(description='네이버를 거쳐 판매처로 가는 예약 링크 (tivan.naver.com)')
    ] = Field(default=None, alias='bookingUrl')


class NaverBookingPartnerModel(ResponseModel):
    ota_id: Annotated[
        str,
        Field(description='판매처 코드')
    ] = Field(alias='otaId')
    exp_name: Annotated[
        str | None,
        Field(description='판매처 이름')
    ] = Field(default=None, alias='expName')


class NaverBookingPartnersDataModel(ResponseModel):
    booking_partners: Annotated[
        list[NaverBookingPartnerModel] | None,
        Field(description='판매처 코드 → 이름')
    ] = Field(default=None, alias='bookingPartners')


class NaverBookingPartnersResponseModel(NaverGraphQLResponseModel):
    """GraphQL `bookingPartners` 응답."""

    data: Annotated[
        NaverBookingPartnersDataModel | None,
        Field(description='결과. 에러면 null')
    ] = None
