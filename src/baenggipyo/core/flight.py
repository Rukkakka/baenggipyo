"""항공권 공통 정의: 소스·여정·좌석 등급 이름, 출력 안내 문구, 검색 조건 베이스, 소스와 상관없는 출력 모델, 구간 공항 해석 묶기와 후보 고르기.

출력 규칙은 references/output-contract.md '항공권 출력' 을 따른다. 금액은 원 정수(`*_krw`), 시각은 각 공항의 현지 시각
`YYYY-MM-DDTHH:MM`, 소요 시간은 분이다. 그 항공권·조회에 원래 없는 값은 `not_applicable_fields()` 로 키째 뺀다.
"""

import re
from collections.abc import Callable
from datetime import (
    timedelta,
    timezone,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    ValidationInfo,
    field_validator,
    model_serializer,
)

from baenggipyo.core.errors import (
    AirportAmbiguousError,
    AirportNotFoundError,
)
from baenggipyo.core.schema import RequestModel

from typing import (
    Annotated,
    Any,
    Literal,
    TypeVar,
)


PlaceT = TypeVar('PlaceT')
CandidateT = TypeVar('CandidateT')
OfferT = TypeVar('OfferT', bound='Offer')

KST: timezone = timezone(timedelta(hours=9), 'KST')

NOTICE: str = (
    '가격은 조회 시점 기준이며 예약 단계에서 바뀔 수 있습니다. '
    '예약·결제는 각 url 의 원 사이트에서 하세요. baenggipyo 는 탐색용입니다.'
)

Source = Literal['naver', 'skyscanner']
TripType = Literal['편도', '왕복', '다구간']
CabinClass = Literal['일반석', '프리미엄일반석', '비즈니스석', '일등석']
LocationType = Literal['공항', '도시']
SellerType = Literal['항공사', '여행사']

CABIN_CLASSES: tuple[CabinClass, ...] = ('일반석', '프리미엄일반석', '비즈니스석', '일등석')

IATA_CODE_PATTERN: re.Pattern[str] = re.compile(r'^[A-Za-z]{3}$')
# 모호한 공항 에러에 싣는 후보 수
MAX_CANDIDATES: int = 5
# 항공권 ID 가 승객 수를 유형별 한 자리 숫자로 담아 조건 모델은 유형별 9명까지 받는다. 소스 요청 모델의 상한·CLI 합계 상한과는 뜻이 달라 따로 둔다
MAX_PASSENGERS_PER_TYPE: int = 9
# 요약 출력(`--brief`)을 출력 모델에 알리는 직렬화 context 키 (`FlightModel.brief_omitted_fields`)
BRIEF_CONTEXT_KEY: str = 'brief'

COMPACT_DATE_PATTERN: re.Pattern[str] = re.compile(r'^(\d{4})(\d{2})(\d{2})$')
COMPACT_DATETIME_PATTERN: re.Pattern[str] = re.compile(r'^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})$')


def compact_date(value: str | None) -> str | None:
    """`YYYYMMDD` 를 `YYYY-MM-DD` 로 바꾼다. 형식이 다르면 None."""
    match = COMPACT_DATE_PATTERN.fullmatch(value or '')
    return '-'.join(match.groups()) if match else None


def compact_datetime(value: str | None) -> str | None:
    """`YYYYMMDDHHmm` 을 현지 시각 `YYYY-MM-DDTHH:MM` 으로 바꾼다. 형식이 다르면 None."""
    match = COMPACT_DATETIME_PATTERN.fullmatch(value or '')
    if match is None:
        return None
    year, month, day, hour, minute = match.groups()
    return f'{year}-{month}-{day}T{hour}:{minute}'


def seconds_to_minutes(seconds: int | None) -> int | None:
    return None if seconds is None else round(seconds / 60)


def trip_type_of(routes: list[tuple[str, str]]) -> TripType:
    """(출발, 도착) 구간들로 편도·왕복·다구간을 정한다. 2구간이라도 처음 출발지로 돌아오지 않으면 다구간이다."""
    if len(routes) == 1:
        return '편도'
    if len(routes) == 2 and routes[0] == (routes[1][1], routes[1][0]):
        return '왕복'
    return '다구간'


def resolve_route(raw_legs: list[tuple[str, str, str]], resolve: Callable[[str], PlaceT]) -> list[tuple[PlaceT, PlaceT, str]]:
    """(출발, 도착, YYYY-MM-DD) 구간마다 출발·도착 입력을 `resolve`(소스의 공항 해석, 요청 1회)로 장소로 바꾼다.

    같은 입력(앞뒤 공백·대소문자 무시)은 한 번만 묻는다. 그래서 왕복 `ICN NRT` 는 해석 요청이 2회다.
    """
    resolved: dict[str, PlaceT] = {}

    def place(text: str) -> PlaceT:
        key = text.strip().upper()
        if key not in resolved:
            resolved[key] = resolve(text)
        return resolved[key]

    return [(place(origin), place(destination), date) for origin, destination, date in raw_legs]


class FlightConditions(RequestModel):
    """어떤 항공권을 찾을지 (승객·좌석·직항·항공사). 소스마다 `<Source>FlightConditions` 로 상속한다.

    직항·항공사 조건을 서버가 거르는지, 받은 뒤 baenggipyo 가 거르는지는 소스마다 다르다 (상속한 모델 docstring).
    """

    adults: Annotated[
        int,
        Field(ge=1, le=MAX_PASSENGERS_PER_TYPE, description='성인 수')
    ] = 1
    children: Annotated[
        int,
        Field(ge=0, le=MAX_PASSENGERS_PER_TYPE, description='소아 수 (만 2~11세)')
    ] = 0
    infants: Annotated[
        int,
        Field(ge=0, le=MAX_PASSENGERS_PER_TYPE, description='유아 수 (만 2세 미만)')
    ] = 0
    cabin: Annotated[
        CabinClass,
        Field(description='좌석 등급')
    ] = '일반석'
    direct: Annotated[
        bool,
        Field(description='직항만')
    ] = False
    airlines: Annotated[
        list[str],
        Field(description='항공사 IATA 코드')
    ] = []

    @field_validator('infants')
    @classmethod
    def infants_need_adults(cls, value: int, info: ValidationInfo) -> int:
        # 항공권 ID 에서 되돌린 조건도 막는다 (CLI 검색 입력은 validate_passengers 가 먼저 막는다). 네이버 국내선 검색·판매처 조회와 스카이스캐너
        # 요청 모델에는 이 검사가 없어, 여기서 막지 않으면 유아가 성인보다 많은 ID 로 가격·예약 링크를 낸다 (2026-10-12 실측 네이버 국내선 D-109-…)
        adults = info.data.get('adults', 1)
        if value > adults:
            raise ValueError(f'유아는 성인 수({adults})보다 많을 수 없습니다')
        return value

    @property
    def only_one_adult(self) -> bool:
        return self.adults == 1 and self.children == 0 and self.infants == 0


class FlightModel(BaseModel):
    """출력 모델 공통 베이스. 해당 없는 필드(`not_applicable_fields`)는 출력에서 키째 뺀다.

    요약(`--brief`, 직렬화 context 의 `BRIEF_CONTEXT_KEY`)이면 `brief_omitted_fields` 도 뺀다. 모양은 그대로라 요약을 읽는 쪽도
    같은 경로로 읽는다.
    """

    model_config = ConfigDict(
        extra='forbid',
    )

    def not_applicable_fields(self) -> frozenset[str]:
        """이 값에 원래 없는 필드. 서브클래스가 넓힌다."""
        return frozenset()

    def brief_omitted_fields(self) -> frozenset[str]:
        """요약에서 빼는 필드 (같은 결과의 다른 곳에 있거나 고르는 데 필요 없는 값). 서브클래스가 넓힌다."""
        return frozenset()

    @model_serializer(mode='wrap')
    def drop_fields(self, handler: SerializerFunctionWrapHandler, info: SerializationInfo) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        omitted = self.not_applicable_fields()
        if info.context and info.context.get(BRIEF_CONTEXT_KEY):
            omitted = omitted | self.brief_omitted_fields()
        for field in omitted:
            data.pop(field, None)
        return data


class Airport(FlightModel):
    """공항·도시 후보 하나 (`search airports`)."""

    code: Annotated[
        str,
        Field(description='IATA 공항·도시 코드')
    ]
    type: Annotated[
        LocationType,
        Field(description='공항 또는 도시. 도시 코드는 그 도시의 모든 공항')
    ]
    name: Annotated[
        str | None,
        Field(description='공항 이름 (도시면 도시 이름)')
    ] = None
    city: Annotated[
        str | None,
        Field(description='도시 이름')
    ] = None
    country: Annotated[
        str | None,
        Field(description='나라 이름')
    ] = None
    airports: Annotated[
        list[str] | None,
        Field(description='도시에 속한 공항 코드. 공항이면 키가 없다')
    ] = None
    domestic: Annotated[
        bool | None,
        Field(description='한국 국내 공항·도시인지')
    ] = None

    def not_applicable_fields(self) -> frozenset[str]:
        return frozenset({'airports'}) if self.type == '공항' else frozenset()


class AirportSearchResult(BaseModel):
    """`search airports` 결과 (두 소스 같은 모양)."""

    model_config = ConfigDict(
        extra='forbid',
    )

    query: Annotated[
        str,
        Field(description='검색어')
    ]
    returned: Annotated[
        int,
        Field(description='돌려준 후보 수')
    ]
    airports: Annotated[
        list[Airport],
        Field(description='공항·도시 후보 (소스가 준 순서. 네이버는 도시가 그 도시의 공항보다 먼저)')
    ]


def candidate_label(airport: Airport) -> str:
    """모호한 공항 에러의 후보 표기 (`NRT 나리타 (공항, 일본)`). 코드가 맨 앞이고, 소스가 주지 않은 이름·나라는 뺀다."""
    detail = ', '.join(part for part in (airport.type, airport.country) if part)
    return ' '.join(part for part in (airport.code, airport.name) if part) + f' ({detail})'


def pick_place(text: str, candidates: list[CandidateT], to_airport: Callable[[CandidateT], Airport]) -> CandidateT:
    """출발·도착지 입력에 맞는 자동완성 후보 하나를 고른다. 두 소스가 같은 규칙을 쓴다.

    - 3글자 코드면 같은 코드의 공항을 먼저 고르고(ICN, BKK), 공항이 없으면 도시(SEL, TYO)를 고른다.
    - 이름이면 이름이 똑같은 도시, 없으면 이름이 똑같은 공항을 고른다. 그래도 정하지 못하면 후보가 하나일 때만 그것을 쓴다.
    - 이름이 똑같은 후보가 여럿이면 고르지 않는다 (네이버 '포틀랜드' → 도시 PDX·PWM 이 이름까지 같다, 2026-10-12 실측).
    - 3글자 입력이 어느 후보의 코드·이름과도 맞지 않으면 후보가 하나여도 쓰지 않는다. 코드로 준 것이라 다른 곳이다
      (네이버 'SEO' → 서울 SEL 하나뿐이라 서울로 찾았다, 2026-10-11 실측).

    Args:
        text: 사용자 입력.
        candidates: 소스 자동완성 후보 (검색에 쓸 수 없는 후보는 미리 뺀 것).
        to_airport: 후보를 `Airport` 로 바꾸는 소스 함수. 코드·종류·이름(도시는 `city`, 공항은 `name`)으로 고른다.

    Raises:
        AirportNotFoundError: 후보가 없거나, 3글자 코드에 맞는 후보가 없을 때 (hint 에 비슷한 후보).
        AirportAmbiguousError: 이름에 맞는 후보가 여럿이거나, 이름이 똑같은 도시(없으면 공항)가 여럿일 때.
    """
    text = text.strip()
    airports = [(candidate, to_airport(candidate)) for candidate in candidates]
    is_code = IATA_CODE_PATTERN.fullmatch(text) is not None
    if is_code:
        code = text.upper()
        for kind in ('공항', '도시'):
            for candidate, airport in airports:
                if airport.type == kind and airport.code == code:
                    return candidate
    for kind in ('도시', '공항'):
        exact = [
            (candidate, airport) for candidate, airport in airports
            if airport.type == kind and (airport.city if kind == '도시' else airport.name) == text
        ]
        if len(exact) == 1:
            return exact[0][0]
        if exact:
            raise AirportAmbiguousError(text, [candidate_label(airport) for _, airport in exact[:MAX_CANDIDATES]], len(exact))
    if not candidates:
        raise AirportNotFoundError(text)
    labels = [candidate_label(airport) for _, airport in airports[:MAX_CANDIDATES]]
    if is_code:
        raise AirportNotFoundError(
            text,
            hint=f"'{text.upper()}' 코드의 공항·도시가 없습니다. 비슷한 후보: {', '.join(labels)}. 맞는 곳이 있으면 그 코드로 다시 주세요",
        )
    if len(candidates) == 1:
        return candidates[0]
    raise AirportAmbiguousError(text, labels, len(candidates))


class SearchLeg(FlightModel):
    """검색한 구간 하나 (`search flights` 결과의 `search.legs[]`)."""

    origin: Annotated[
        str,
        Field(description='출발 코드')
    ]
    destination: Annotated[
        str,
        Field(description='도착 코드')
    ]
    date: Annotated[
        str,
        Field(description='출발 날짜')
    ]


class SearchSummary(BaseModel):
    """어떤 조건으로 찾았는지 (`search flights` 결과의 `search`). 소스에만 있는 필드는 서브클래스에 둔다."""

    model_config = ConfigDict(
        extra='forbid',
    )

    trip_type: Annotated[
        TripType,
        Field(description='편도·왕복·다구간')
    ]
    legs: Annotated[
        list[SearchLeg],
        Field(description='구간')
    ]
    cabin: Annotated[
        CabinClass,
        Field(description='좌석 등급')
    ]
    adults: Annotated[
        int,
        Field(description='성인 수')
    ]
    children: Annotated[
        int,
        Field(description='소아 수')
    ]
    infants: Annotated[
        int,
        Field(description='유아 수')
    ]
    direct: Annotated[
        bool,
        Field(description='직항만 찾았는지')
    ]
    url: Annotated[
        str,
        Field(description='원 사이트 검색 결과 페이지')
    ]


class Segment(FlightModel):
    """비행 한 번."""

    airline_code: Annotated[
        str | None,
        Field(description='판매(표기) 항공사 IATA 코드 (모르면 null)')
    ]
    airline: Annotated[
        str | None,
        Field(description='판매(표기) 항공사 이름')
    ] = None
    flight_number: Annotated[
        str | None,
        Field(description='편명 (예: KE703). 항공사 코드나 편 번호를 모르면 null')
    ]
    operating_airline_code: Annotated[
        str | None,
        Field(description='실제 운항 항공사 코드. 공동운항(코드셰어)일 때만 키가 있다')
    ] = None
    operating_airline: Annotated[
        str | None,
        Field(description='실제 운항 항공사 이름. 공동운항일 때만 키가 있다')
    ] = None
    departure_airport: Annotated[
        str | None,
        Field(description='출발 공항 IATA 코드 (모르면 null)')
    ]
    arrival_airport: Annotated[
        str | None,
        Field(description='도착 공항 IATA 코드 (모르면 null)')
    ]
    departure_at: Annotated[
        str | None,
        Field(description='출발 시각 (출발 공항 현지 시각, YYYY-MM-DDTHH:MM)')
    ] = None
    arrival_at: Annotated[
        str | None,
        Field(description='도착 시각 (도착 공항 현지 시각, YYYY-MM-DDTHH:MM)')
    ] = None
    duration_minutes: Annotated[
        int | None,
        Field(description='비행 시간 (분)')
    ] = None
    aircraft: Annotated[
        str | None,
        Field(description='기종')
    ] = None

    def not_applicable_fields(self) -> frozenset[str]:
        codeshare = self.operating_airline_code is not None and self.operating_airline_code != self.airline_code
        return frozenset() if codeshare else frozenset({'operating_airline_code', 'operating_airline'})

    def brief_omitted_fields(self) -> frozenset[str]:
        # 편명·항공사(코드셰어면 운항 항공사)만 남긴다. 코드는 편명 앞에 있고, 공항·시각은 여정과 경유 공항에 있다 (직항이면 여정과 똑같다)
        return frozenset({
            'airline_code', 'operating_airline_code', 'departure_airport', 'arrival_airport',
            'departure_at', 'arrival_at', 'duration_minutes', 'aircraft',
        })


class Journey(FlightModel):
    """한 방향 여정 (편도 하나, 왕복의 가는 편·오는 편, 다구간의 한 구간)."""

    departure_airport: Annotated[
        str | None,
        Field(description='여정 출발 공항 IATA 코드 (모르면 null)')
    ]
    arrival_airport: Annotated[
        str | None,
        Field(description='여정 도착 공항 IATA 코드 (모르면 null)')
    ]
    departure_at: Annotated[
        str | None,
        Field(description='여정 출발 시각 (현지 시각)')
    ] = None
    arrival_at: Annotated[
        str | None,
        Field(description='여정 도착 시각 (현지 시각)')
    ] = None
    duration_minutes: Annotated[
        int | None,
        Field(description='경유 대기를 포함한 전체 소요 시간 (분)')
    ] = None
    stops: Annotated[
        int,
        Field(ge=0, description='경유 횟수 (0 = 직항)')
    ]
    stopover_airports: Annotated[
        list[str | None],
        Field(description='경유 공항 코드 (직항이면 [], 모르는 공항은 null)')
    ]
    segments: Annotated[
        list[Segment],
        Field(description='비행 구간')
    ]


class Itinerary(FlightModel):
    """검색 결과 항공권 한 건 (`search flights`)."""

    source: Annotated[
        Source,
        Field(description='항공권을 가져온 소스')
    ]
    id: Annotated[
        str,
        Field(description='항공권 ID. 같은 소스의 flight 명령에 그대로 넣는다')
    ]
    url: Annotated[
        str,
        Field(description='원 사이트에서 이 항공권을 여는 링크')
    ]
    trip_type: Annotated[
        TripType,
        Field(description='편도·왕복·다구간')
    ]
    cabin: Annotated[
        CabinClass,
        Field(description='좌석 등급')
    ]
    journeys: Annotated[
        list[Journey],
        Field(description='여정 (편도 1개, 왕복 2개, 다구간은 구간 수만큼)')
    ]
    total_price_krw: Annotated[
        int | None,
        Field(description='모든 승객 합계 (세금·유류할증료 포함, 원)')
    ] = None
    price_per_adult_krw: Annotated[
        int | None,
        Field(description='성인 1인 요금 (세금·유류할증료 포함, 원)')
    ] = None
    offer_count: Annotated[
        int | None,
        Field(description='판매처 수')
    ] = None
    cheapest_seller: Annotated[
        str | None,
        Field(description='대표 가격의 판매처')
    ] = None
    baggage_included: Annotated[
        bool | None,
        Field(description='대표 운임에 위탁 수하물이 포함되는지 (모르면 null)')
    ] = None

    def brief_omitted_fields(self) -> frozenset[str]:
        # 소스·여정 종류·좌석 등급은 결과의 search 에 있고, 항공권 링크는 flight <id> 나 search.url 로 연다
        return frozenset({'source', 'url', 'trip_type', 'cabin'})


class Offer(FlightModel):
    """판매처 하나의 가격 (`flight` 의 `offers[]`)."""

    seller: Annotated[
        str | None,
        Field(description='판매처 이름')
    ] = None
    seller_type: Annotated[
        SellerType | None,
        Field(description='항공사 직판 또는 여행사')
    ] = None
    total_price_krw: Annotated[
        int | None,
        Field(description='모든 승객 합계 (원)')
    ] = None
    price_per_adult_krw: Annotated[
        int | None,
        Field(description='성인 1인 요금 (원)')
    ] = None
    fare_name: Annotated[
        str | None,
        Field(description='운임 이름 (결제수단 조건 등)')
    ] = None
    baggage_included: Annotated[
        bool | None,
        Field(description='위탁 수하물 포함 여부 (모르면 null)')
    ] = None
    url: Annotated[
        str | None,
        Field(description='이 판매처의 예약 페이지로 가는 링크. --offer-urls 로 뺀 운임은 키가 없다')
    ] = None
    url_omitted: Annotated[
        bool,
        Field(exclude=True, description='--offer-urls 로 예약 링크를 뺀 운임인지 (출력하지 않음)')
    ] = False

    def not_applicable_fields(self) -> frozenset[str]:
        # 링크를 모르는 것(null)과 달리 빼 달라고 한 것이라 키째 뺀다
        return frozenset({'url'}) if self.url_omitted else frozenset()


def shown_offers(fares: list[list[OfferT]], limit: int, url_limit: int | None = None) -> list[OfferT]:
    """판매처 운임 묶음을 앞에서 `limit` 개만 `offers` 줄로 편다. `url_limit` 을 주면 그 뒤 운임은 예약 링크 키를 뺀다.

    판매처 여러 곳에서 따로 예약하는 조합은 운임 하나가 여러 줄이다. 개수와 링크를 모두 묶음으로 세어 한 조합의 줄을 가르지 않는다
    (링크가 일부 줄에만 남으면 나머지 구간을 어디서 사는지 모른다).
    """
    return [
        row if url_limit is None or index < url_limit else row.model_copy(update={'url_omitted': True})
        for index, rows in enumerate(fares[:limit])
        for row in rows
    ]


class CalendarDay(FlightModel):
    """날짜별 최저가 한 칸 (`calendar`)."""

    date: Annotated[
        str,
        Field(description='출발 날짜 (YYYY-MM-DD)')
    ]
    return_date: Annotated[
        str | None,
        Field(description='오는 날짜. 왕복일 때만 키가 있다')
    ] = None
    min_price_krw: Annotated[
        int | None,
        Field(description='그날 최저가 (원, 추정가)')
    ] = None
    direct: Annotated[
        bool | None,
        Field(description='최저가가 직항인지 (모르면 null)')
    ] = None
    round_trip: Annotated[
        bool,
        Field(exclude=True, description='왕복 달력인지 (출력하지 않음)')
    ] = False

    def not_applicable_fields(self) -> frozenset[str]:
        return frozenset() if self.round_trip else frozenset({'return_date'})


class Destination(FlightModel):
    """어디든지 결과의 목적지 하나 (`explore`)."""

    code: Annotated[
        str | None,
        Field(description='목적지 코드 (네이버는 IATA 공항 코드, 스카이스캐너는 자체 도시 코드). 모르면 null')
    ]
    city: Annotated[
        str | None,
        Field(description='도시 이름')
    ] = None
    country: Annotated[
        str | None,
        Field(description='나라 이름')
    ] = None
    country_code: Annotated[
        str | None,
        Field(description='나라 코드 (ISO 3166-1 alpha-2)')
    ] = None
    min_price_krw: Annotated[
        int | None,
        Field(description='최저가 (원, 추정가)')
    ] = None
    direct: Annotated[
        bool | None,
        Field(description='최저가가 직항인지 (모르면 null)')
    ] = None
    departure_date: Annotated[
        str | None,
        Field(description='최저가의 출발 날짜 (YYYY-MM-DD)')
    ] = None
    return_date: Annotated[
        str | None,
        Field(description='최저가의 오는 날짜. 왕복일 때만 키가 있다')
    ] = None
    round_trip: Annotated[
        bool,
        Field(exclude=True, description='왕복 조회인지 (출력하지 않음)')
    ] = False

    def not_applicable_fields(self) -> frozenset[str]:
        return frozenset() if self.round_trip else frozenset({'return_date'})
