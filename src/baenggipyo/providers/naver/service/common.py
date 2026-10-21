"""두 모듈 이상이 쓰는 결과 모델·코드표·URL·안내 문구."""

import json
import logging
import time
from urllib.parse import urlencode

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from baenggipyo.core.errors import BaenggipyoError
from baenggipyo.core.flight import (
    CabinClass,
    FlightConditions,
    Itinerary,
    Offer,
    SearchSummary,
    SellerType,
    TripType,
)
from baenggipyo.core.flight import trip_type_of as route_trip_type
from baenggipyo.core.ratelimit import (
    state_dir,
    write_json_atomic,
)
from baenggipyo.providers.naver.api.client import NaverClient
from baenggipyo.providers.naver.api.request.common import (
    NaverBookingPartnersRequestPayloadModel,
    NaverLocationType,
    NaverSeatClass,
    NaverTripCode,
)
from baenggipyo.providers.naver.api.response.common import NaverGraphQLResponseModel

from typing import (
    Annotated,
    Generic,
    Literal,
    TypeVar,
)


T = TypeVar('T')
ItineraryT = TypeVar('ItineraryT', bound='NaverItinerary')

logger = logging.getLogger(__name__)

SITE_URL: str = 'https://flight.naver.com'

SEAT_CLASS_BY_CABIN: dict[CabinClass, NaverSeatClass] = {
    '일반석': 'Y',
    '프리미엄일반석': 'P',
    '비즈니스석': 'C',
    '일등석': 'F',
}
# 항공권 ID 처럼 문자열에서 되돌릴 때 쓰므로 키를 str 로 둔다
CABIN_BY_SEAT_CLASS: dict[str, CabinClass] = {code: label for label, code in SEAT_CLASS_BY_CABIN.items()}
TRIP_CODE_BY_TYPE: dict[TripType, NaverTripCode] = {
    '편도': 'OW',
    '왕복': 'RT',
    '다구간': 'MD',
}

ESTIMATE_NOTE: str = (
    '이 가격은 실시간이 아니라 네이버가 최근 검색에서 모은 최저가이고, 특정 카드로만 받는 할인가가 섞일 수 있습니다. '
    '고른 날짜·목적지는 search flights 로 실제 가격을 확인하세요'
)


class NaverLocation(BaseModel):
    """검색에 넣을 공항·도시 하나 (자동완성으로 정한 값)."""

    model_config = ConfigDict(
        extra='forbid',
    )

    code: Annotated[
        str,
        Field(description='IATA 공항·도시 코드')
    ]
    type: Annotated[
        NaverLocationType,
        Field(description='airport 또는 city')
    ]
    name: Annotated[
        str | None,
        Field(description='공항 또는 도시 이름')
    ] = None
    domestic: Annotated[
        bool | None,
        Field(description='한국 국내인지')
    ] = None
    airports: Annotated[
        list[str] | None,
        Field(description='도시에 속한 공항 코드 (도시만)')
    ] = None


class NaverLeg(BaseModel):
    """여정 한 구간: 출발지·도착지·날짜."""

    model_config = ConfigDict(
        extra='forbid',
    )

    origin: Annotated[
        NaverLocation,
        Field(description='출발지')
    ]
    destination: Annotated[
        NaverLocation,
        Field(description='도착지')
    ]
    date: Annotated[
        str,
        Field(pattern=r'^\d{4}-\d{2}-\d{2}$', description='출발 날짜 (YYYY-MM-DD)')
    ]

    @property
    def compact_date(self) -> str:
        return self.date.replace('-', '')


def trip_type_of(legs: list[NaverLeg]) -> TripType:
    return route_trip_type([(leg.origin.code, leg.destination.code) for leg in legs])


def results_url(
    legs: list[NaverLeg],
    *,
    adults: int,
    children: int,
    infants: int,
    cabin: CabinClass,
    direct: bool,
    selected: str | None = None,
) -> str:
    """사이트 검색 결과 페이지 URL. `selected` 를 주면 그 항공권이 골라진 채로 열린다 (2026-10-11 실측)."""
    routes = '/'.join(
        f'{leg.origin.code}:{leg.origin.type}-{leg.destination.code}:{leg.destination.type}-{leg.compact_date}'
        for leg in legs
    )
    query: dict[str, str | int] = {'adult': adults, 'fareType': SEAT_CLASS_BY_CABIN[cabin]}
    if children:
        query['child'] = children
    if infants:
        query['infant'] = infants
    if direct:
        query['isDirect'] = 'true'
    if selected:
        query['selectedFlight'] = selected
    return f'{SITE_URL}/flights/international/{routes}?{urlencode(query)}'


def unwrap_graphql(response: NaverGraphQLResponseModel, value: T | None, what: str) -> T:
    """GraphQL 응답에서 결과(`value`, 응답의 `data` 에서 꺼낸 값)를 꺼낸다.

    잘못된 변수(`BAD_USER_INPUT`)는 입력을 CLI 에서 미리 검사하므로 사이트 API 가 바뀐 경우다.

    Raises:
        BaenggipyoError: GraphQL 에러가 있거나 결과가 없을 때 (`UPSTREAM_ERROR`).
    """
    if response.errors or value is None:
        message = next((error.message for error in response.errors if error.message), None)
        codes = sorted({str((error.extensions or {}).get('code')) for error in response.errors if error.extensions})
        raise BaenggipyoError(
            code='UPSTREAM_ERROR',
            message=f'네이버 {what} 조회가 실패했습니다' + (f': {message}' if message else '') + (f' ({", ".join(codes)})' if codes else ''),
            hint='네이버 항공권 API 가 바뀌었을 수 있습니다. 스카이스캐너를 쓰거나 baenggipyo 를 업데이트하세요',
        )
    return value


NaverFlightSort = Literal['가격', '출발시각', '소요시간']

# 서버 정렬 키. 셋 다 서버가 적용한다 (2026-10-11 실측 ICN→NRT: departureTime 은 출발 시각순, departureDurationSecond 는 소요
# 시간순이고 같으면 가격순). 그래서 받은 순서를 그대로 쓰고 다시 세우거나 확인하지 않는다. 가격순만 서버 기준(카드 할인 포함
# 최저가)이 대표 가격(A01)과 달라 search_flights 가 순서를 보고 notes 로 알린다
SORT_BY_LABEL: dict[NaverFlightSort, dict[str, int]] = {
    '가격': {'adultMinFare': 1},
    '출발시각': {'departureTime': 1},
    '소요시간': {'departureDurationSecond': 1},
}
FLIGHT_SORTS: tuple[NaverFlightSort, ...] = tuple(SORT_BY_LABEL)

# 캐시 조회(initialRequest false)에 version 을 빼면 그 조건의 가장 새 검색 결과가 온다. 서버는 같은 조건의 검색마다 version 을 올리고
# version 별 캐시를 따로 두어 각각 약 15분 뒤 지운다. 그래서 다른 사용자도 찾는 노선은 version 0 캐시가 먼저 사라져, version 0 으로
# 물으면 방금 검색했어도 isExpired 가 온다 (ICN→NRT: v0 만료·v1·v2·생략=v2, 김포↔제주: 생략=최신, 2026-10-11 실측). flight 는 version 을 뺀다
CACHE_LATEST_VERSION: None = None

DEFAULT_LIMIT: int = 10
# limit 은 운임 묶음 수만 줄이고 여정 목록은 늘 전부 온다. 50 이면 편도 응답이 수 MB 안쪽이다
MAX_LIMIT: int = 50
# 판매처 링크(tivan.naver.com)가 하나에 2~3KB 라 다 내보내면 출력이 너무 길다 (왕복 한 건 64곳, 2026-10-11 실측)
DEFAULT_OFFER_LIMIT: int = 10
MAX_OFFER_LIMIT: int = 100

# A01 은 '성인/모든 결제수단', A01/Bxx 는 특정 카드·간편결제로만 받는 할인 운임이다 (status.fareTypesCodeMap)
GENERAL_FARE_TYPE: str = 'A01'
CARD_FARE_PREFIX: str = 'A01/'

AIRLINE_OTA_TYPES: frozenset[str] = frozenset({'KR_AIRLINE', 'GLOBAL_AIRLINE'})

PARTNER_NAMES_FILE: str = 'naver/booking_partners.json'
PARTNER_NAMES_TTL_SECONDS: float = 86400.0


class NaverFlightConditions(FlightConditions):
    """네이버 검색 조건. 직항·항공사는 서버 필터로 보낸다."""


class NaverItinerary(Itinerary):
    """네이버 항공권 한 건. 카드·간편결제 할인 최저가를 대표 가격과 따로 준다."""

    card_discount_price_krw: Annotated[
        int | None,
        Field(description='특정 카드·간편결제로만 받는 성인 1인 최저가 (원). 대표 가격보다 싼 할인이 없으면 키가 없다')
    ] = None
    card_discount_name: Annotated[
        str | None,
        Field(description='할인 결제수단과 조건 (예: 성인/현대카드, 성인/KB국민카드(이용실적 충족시))')
    ] = None
    card_discount_category: Annotated[
        str | None,
        Field(description='할인 분류 (예: 신용카드(즉시할인), 신용카드(실적충족/청구할인), 간편결제)')
    ] = None

    def not_applicable_fields(self) -> frozenset[str]:
        fields = super().not_applicable_fields()
        if self.card_discount_price_krw is None:
            fields = fields | {'card_discount_price_krw', 'card_discount_name', 'card_discount_category'}
        return fields


class NaverOffer(Offer):
    """판매처 하나의 운임. 결제수단 조건이 있는 운임은 `payment_condition` 을 단다."""

    payment_condition: Annotated[
        str | None,
        Field(description='결제수단 조건 분류 (예: 신용카드(즉시할인)). 모든 결제수단 운임이면 키가 없다')
    ] = None

    def not_applicable_fields(self) -> frozenset[str]:
        fields = super().not_applicable_fields()
        return fields | {'payment_condition'} if self.payment_condition is None else fields


class NaverFlightSearchResult(BaseModel, Generic[ItineraryT]):
    """`search flights` 결과. 국제선은 `NaverItinerary`, 국내선 편도는 `NaverDomesticItinerary` 로 매긴다 (같은 모양)."""

    model_config = ConfigDict(
        extra='forbid',
    )

    search: Annotated[
        SearchSummary,
        Field(description='검색 조건')
    ]
    returned: Annotated[
        int,
        Field(description='돌려준 항공권 수')
    ]
    itineraries: Annotated[
        list[ItineraryT],
        Field(description='항공권 (정렬 순서대로)')
    ]
    notes: Annotated[
        list[str],
        Field(exclude=True, description='결과 안내 (emit 의 notes 로 낸다)')
    ] = []


def cut_short(completed: bool | None) -> bool:
    """시간 상한에 닿아 끝나기 전에 끊은 검색인지. 국제선(`isCompleted`)·국내선(`isComplete`)이 같이 쓴다.

    끊은 스트림의 마지막 이벤트만 완료 표시가 false 다. 완료 표시가 없으면(None) 끝났는지 알 수 없어 끊긴 것으로 보지 않는다.
    """
    return completed is False


def incomplete_note(completed: bool | None, done: int | None, total: int | None, *, missing: str = '더 싼 항공권') -> list[str]:
    """검색이 끝나기 전에 스트림을 끊었으면(`cut_short`) 알린다.

    Args:
        completed: 마지막 이벤트의 완료 표시.
        done: 응답한 판매처 수.
        total: 검색한 판매처 수.
        missing: 빠졌을 수 있는 것 (받침으로 끝나는 말, 뒤에 '이' 를 붙인다). `flight` 는 항공권이 아니라 판매처 운임이 빠진다.
    """
    if not cut_short(completed):
        return []
    progress = f'판매처 {done}/{total}곳이 응답한 뒤 ' if done is not None and total is not None else ''
    return [f'{progress}시간 상한에 닿아, 받은 데까지만 보여 줍니다. {missing}이 빠졌을 수 있습니다']


def seller_type_of(ota_type: str | None) -> SellerType | None:
    """판매처 조회의 판매처 유형(`otaType`)을 항공사·여행사로. 유형이 없으면 None."""
    if not ota_type:
        return None
    return '항공사' if ota_type in AIRLINE_OTA_TYPES else '여행사'


def flight_number_of(airline_code: str, number: str | None) -> str | None:
    # 네이버는 편명 숫자 앞에 0 을 붙인다 (0605 → ZE605). 편 번호가 없으면 없는 편명(KE0)을 만들지 않고 모름(None)으로 둔다
    if not number:
        return None
    digits = number.lstrip('0') or '0'
    return f'{airline_code}{digits}'


def partner_names(client: NaverClient) -> dict[str, str]:
    """판매처 코드 → 이름 (`bookingPartners`). 검색 응답에는 판매처 이름표가 없어 따로 받는다.

    가격이 아닌 이름표(26곳, 거의 바뀌지 않는다)라 하루 동안 상태 디렉터리에 두고 다시 쓴다. 그래서 검색마다 요청이 1회
    줄어든다. 파일을 읽거나 쓰지 못하면 매번 받는다.

    표시용 이름이라 GraphQL 이 실패하거나 빈 이름표가 와도 검색을 막지 않고 빈 표를 돌려준다 (판매처 이름이 null 이 될 뿐이다).
    빈 이름표는 저장하지 않는다. 저장하면 하루 동안 모든 검색의 판매처 이름이 빠진다. 요청 자체가 실패하면(HTTP·네트워크·요청 제한)
    같은 호스트의 검색도 실패하므로 그대로 올린다.
    """
    path = state_dir() / PARTNER_NAMES_FILE
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
        # 받은 시각이 미래면(시계가 뒤로 감, 손으로 고친 파일) 언제 받았는지 모르므로 다시 받는다. 두면 그 시간만큼 옛 이름표를 쓴다
        if 0 <= time.time() - float(saved['fetched_at']) < PARTNER_NAMES_TTL_SECONDS:
            return {str(code): str(name) for code, name in saved['names'].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    response = client.run_request_booking_partners(NaverBookingPartnersRequestPayloadModel())
    try:
        data = unwrap_graphql(response, response.data, '판매처 이름')
    except BaenggipyoError as exc:
        logger.warning('%s. 판매처 이름 없이 이어 갑니다', exc.message)
        return {}
    names = {partner.ota_id: partner.exp_name for partner in data.booking_partners or [] if partner.exp_name}
    if not names:
        logger.warning('네이버 판매처 이름표가 비어 왔습니다. 저장하지 않고 판매처 이름 없이 이어 갑니다')
        return names
    try:
        write_json_atomic(path, {'fetched_at': time.time(), 'names': names})
    except OSError:
        pass
    return names
