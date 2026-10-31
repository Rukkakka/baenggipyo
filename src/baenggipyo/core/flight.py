"""항공권 공통 정의: 소스·여정·좌석 등급 이름, 검색 조건 베이스, 소스와 상관없는 출력 모델, 입력 공항 해석 묶기와 후보 고르기.

출력 규칙은 docs/development/output-contract.md '항공권 출력' 을 따른다. 금액은 원 정수(`*_krw`), 시각은 각 공항의 현지 시각
`YYYY-MM-DDTHH:MM`, 소요 시간은 분이다. 그 항공권·조회에 원래 없는 값은 `not_applicable_fields()` 로 키째 뺀다.
"""

import re
from collections.abc import (
    Callable,
    Sequence,
)
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

Source = Literal['naver', 'skyscanner']
TripType = Literal['편도', '왕복', '다구간']
CabinClass = Literal['일반석', '프리미엄일반석', '비즈니스석', '일등석']
LocationType = Literal['공항', '도시']
SellerType = Literal['항공사', '여행사']

CABIN_CLASSES: tuple[CabinClass, ...] = ('일반석', '프리미엄일반석', '비즈니스석', '일등석')

IATA_CODE_PATTERN: re.Pattern[str] = re.compile(r'^[A-Za-z]{3}$')
# IATA 가 아닌 4글자 코드 (스카이스캐너 도시 코드 CJUA·CYNT, ICAO 공항 코드 RKSI). 대문자로 준 것만 코드로 본다 (Jeju·Rome 같은 이름은 이름이다)
OTHER_CODE_PATTERN: re.Pattern[str] = re.compile(r'^[A-Z][A-Z0-9]{3}$')
# 모호한 공항 에러에 싣는 후보 수
MAX_CANDIDATES: int = 5
# 항공권 ID 가 승객 수를 유형별 한 자리 숫자로 담아 조건 모델은 유형별 9명까지 받는다. 소스 요청 모델의 상한·CLI 합계 상한과는 뜻이 달라 따로 둔다
MAX_PASSENGERS_PER_TYPE: int = 9
# 요약 출력(`--brief`)을 출력 모델에 알리는 직렬화 context 키 (`FlightModel.brief_omitted_fields`)
BRIEF_CONTEXT_KEY: str = 'brief'
# 달력 결과 맨 위 `cheapest_days` 에 싼 순으로 올리는 날 수
CHEAPEST_DAY_COUNT: int = 3
# 출발 시각대 조건(--depart-time·--return-time)이 받는 경계 (시). 네이버 사이트의 출발 시각 칸(00-06·06-09·…·21-24, 서버 필터 값)에 맞춰
# 두 소스가 같은 값을 받는다. 시작 시는 넣고 끝 시는 뺀다 (네이버 국내선 09-12 칸이 09:00 출발을 넣었다, 2026-10-21 실측)
DEPART_HOUR_BOUNDARIES: tuple[int, ...] = (0, 6, 9, 12, 15, 18, 21, 24)

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


def check_place_inputs(*texts: str) -> None:
    """출발·도착지 입력에 IATA 가 아닌 대문자 4글자 코드(`OTHER_CODE_PATTERN`)가 있으면 요청을 보내기 전에 거른다.

    입력만 보고 알 수 있어 자동완성 요청(스카이스캐너는 시간당 상한이 있다)을 쓰지 않는다. 비슷한 후보도 알리지 않는다
    (스카이스캐너 'CJUA'(제주) → 후보 9곳이 청주·청두라 첫 후보 CJJ 를 권하게 된다, 2026-10-20 실측).
    대문자로만 쓴 이름(JEJU·DOHA)도 걸리지만 전에도 찾지 못했다. 자동완성이 이름을 한글로 줘 이름이 똑같은 후보가 없고 후보는 도시·공항
    여럿이라 `AIRPORT_AMBIGUOUS` 였다 (네이버 'JEJU' → 제주 도시·공항, 스카이스캐너 'JEJU' → 제주 공항·도시·휴훼, 2026-10-20 실측.
    네이버 DOHA·OSLO·ROME·MALE 후보 3곳, BALI·LIMA 4곳, NICE 2곳, RKSI 0곳, 2026-10-21 실측).
    소문자(`cjua`)는 이름(Jeju·Rome)과 가릴 수 없어 거르지 않는다. 이름으로 자동완성에 넘기고, 이름이 똑같은 후보가 없으니 후보가 하나일 때만 고른다.

    Raises:
        AirportNotFoundError: IATA 가 아닌 4글자 코드일 때.
    """
    for text in texts:
        text = text.strip()
        if OTHER_CODE_PATTERN.fullmatch(text):
            raise AirportNotFoundError(
                text,
                hint='대문자 4글자(스카이스캐너 sky_code, ICAO 공항 코드, 대문자로만 쓴 이름)는 IATA 코드가 아니라 찾지 않습니다. '
                     '한글 도시 이름(예: 제주)이나 search airports 로 찾은 3글자 IATA 코드로 다시 주세요',
            )


def resolve_inputs(texts: Sequence[str], resolve: Callable[[str], PlaceT]) -> list[PlaceT]:
    """출발·도착지 입력들을 `resolve`(소스의 공항 해석, 요청 1회)로 장소로 바꾼다. 두 소스의 모든 명령이 입력을 이것으로 푼다.

    쓸 수 없는 입력(`check_place_inputs`)이 하나라도 있으면 앞 입력의 요청도 보내기 전에 거른다.
    같은 입력(앞뒤 공백·대소문자 무시)은 한 번만 묻는다. 그래서 왕복 `ICN NRT` 는 해석 요청이 2회다.
    """
    check_place_inputs(*texts)
    resolved: dict[str, PlaceT] = {}
    for text in texts:
        key = text.strip().upper()
        if key not in resolved:
            resolved[key] = resolve(text)
    return [resolved[text.strip().upper()] for text in texts]


def resolve_route(raw_legs: list[tuple[str, str, str]], resolve: Callable[[str], PlaceT]) -> list[tuple[PlaceT, PlaceT, str]]:
    """(출발, 도착, YYYY-MM-DD) 구간마다 출발·도착 입력을 장소로 바꾼다 (`resolve_inputs`)."""
    places = resolve_inputs([text for origin, destination, _ in raw_legs for text in (origin, destination)], resolve)
    return [(places[2 * index], places[2 * index + 1], date) for index, (_, _, date) in enumerate(raw_legs)]


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
    depart_hours: Annotated[
        tuple[int, int] | None,
        Field(description='가는 편(다구간은 첫 구간) 출발 시각대 (시작 시, 끝 시). 항공권 ID 에는 담지 않는다 (항공사 조건과 같다)')
    ] = None
    return_hours: Annotated[
        tuple[int, int] | None,
        Field(description='오는 편(구간이 둘인 검색의 둘째 구간) 출발 시각대 (시작 시, 끝 시). 항공권 ID 에는 담지 않는다')
    ] = None

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

    def leg_hours(self, leg_count: int) -> list[tuple[int, int] | None]:
        """구간마다 출발 시각대 (첫 구간은 `depart_hours`, 둘째 구간은 `return_hours`, 나머지는 조건 없음)."""
        return [(self.depart_hours, self.return_hours)[index] if index < 2 else None for index in range(leg_count)]


def in_hours(local_datetime: str | None, hours: tuple[int, int] | None) -> bool | None:
    """현지 시각 `YYYY-MM-DDTHH:MM` 이 출발 시각대 (시작 시 이상, 끝 시 미만) 안인지. 조건이 없으면 True, 시각을 모르면 None."""
    if hours is None:
        return True
    if not local_datetime or len(local_datetime) < 16:
        return None
    hour = int(local_datetime[11:13])
    return hours[0] <= hour < hours[1]


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
        str | None,
        Field(description='IATA 공항·도시 코드. 소스가 IATA 코드를 주지 않은 도시면 null 이다 (검색에는 이름으로 넣는다)')
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


def _only_airport(city: tuple[CandidateT, Airport], airports: list[tuple[CandidateT, Airport]]) -> CandidateT | None:
    """도시의 공항이 하나뿐이고 그 공항이 후보에 있으면 그 공항 후보. 아니면 None.

    네이버 '인천' 은 공항이 인천 하나뿐인 도시(`ICN`, airports [ICN])인데, 도시로 찾으면 서버가 김포 출발까지 준다 (2026-10-23 실측: 인천→오사카
    왕복 20건 중 김포 출발 피치항공 MM738·MM744 4건, 공항 ICN 으로 찾으면 0건). '인천 출발' 을 찾는데 김포 편이 섞인다. '김포' 는 거꾸로
    인천 출발이 섞인다 (2026-10-24 김포→오사카 편도 50건 중 34건, 공항 GMP 로는 0건). 공항이 하나뿐인 도시는
    그 공항과 뜻이 같아 공항으로 찾는다. 공항 목록을 주지 않는 소스(스카이스캐너)는 바뀌지 않는다.
    """
    codes = city[1].airports or []
    if len(codes) != 1:
        return None
    return next((candidate for candidate, airport in airports if airport.type == '공항' and airport.code == codes[0]), None)


def pick_place(text: str, candidates: list[CandidateT], to_airport: Callable[[CandidateT], Airport]) -> CandidateT:
    """출발·도착지 입력에 맞는 자동완성 후보 하나를 고른다. 두 소스가 같은 규칙을 쓴다.

    - 3글자 코드면 같은 코드의 공항을 먼저 고르고(ICN, BKK), 공항이 없으면 도시(SEL, TYO)를 고른다.
    - 이름이면 이름이 똑같은 도시, 없으면 이름이 똑같은 공항을 고른다. 그래도 정하지 못하면 후보가 하나일 때만 그것을 쓴다.
    - 고른 도시의 공항이 하나뿐이면 그 공항으로 찾는다 (`_only_airport`, 네이버 '인천' → 인천국제공항).
    - 이름이 똑같은 후보가 여럿이면 고르지 않는다 (네이버 '포틀랜드' → 도시 PDX·PWM 이 이름까지 같다, 2026-10-12 실측).
    - 3글자 입력이 어느 후보의 코드·이름과도 맞지 않으면 후보가 하나여도 쓰지 않는다. 코드로 준 것이라 다른 곳이다
      (네이버 'SEO' → 서울 SEL 하나뿐이라 서울로 찾았다, 2026-10-11 실측).
    - 대문자 4글자 코드는 여기 오기 전에 `resolve_inputs` 가 거른다 (자동완성 요청 전에, `check_place_inputs`).

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
            return _only_airport(exact[0], airports) or exact[0][0]
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
        Field(description='실제 운항 항공사 코드. 공동운항(코드셰어)일 때만 키가 있다 (코드를 모르면 null)')
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
        if self.operating_airline_code is not None:
            codeshare = self.operating_airline_code != self.airline_code
        else:
            # 운항 항공사 코드를 모르면(스카이스캐너) 이름으로 가린다. 빼면 이름까지 준 공동운항이 자사편처럼 보인다. 어느 이름이든 모르면
            # 공동운항인지 알 수 없어 뺀다 (판매 항공사 이름을 모를 때 운항 항공사를 달면 자사편이 공동운항처럼 보인다)
            codeshare = self.airline is not None and self.operating_airline is not None and self.operating_airline != self.airline
        return frozenset() if codeshare else frozenset({'operating_airline_code', 'operating_airline'})

    def brief_omitted_fields(self) -> frozenset[str]:
        # 편명·항공사(코드셰어면 운항 항공사)만 남긴다. 코드는 편명 앞에 있고, 공항·시각은 여정과 경유 공항에 있다 (직항이면 여정과 똑같다).
        # 편명을 모르면 판매 항공사 코드를, 운항 항공사 이름을 모르면 운항 항공사 코드를 남긴다 (빼면 요약에서 그 항공사를 알 수 없다)
        omitted = {'departure_airport', 'arrival_airport', 'departure_at', 'arrival_at', 'duration_minutes', 'aircraft'}
        if self.flight_number is not None:
            omitted.add('airline_code')
        if self.operating_airline is not None:
            omitted.add('operating_airline_code')
        return frozenset(omitted)


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
    seller_count: Annotated[
        int | None,
        Field(description='판매처 수. 판매처 여러 곳에서 따로 예약하는 조합은 하나로 센다')
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


def url_omitted_notes(offers: list[OfferT], url_limit: int | None) -> list[str]:
    """`shown_offers` 가 예약 링크를 뺀 운임이 있으면 알린다. `--offer-urls 0` 은 링크를 모두 빼 달라고 한 것이라 알리지 않는다.

    뺀 링크는 키가 없어 출력 규약대로 읽으면 '해당 없음' 이다. `flight` 는 기본으로 링크를 앞 운임 하나에만 붙여, 알리지 않으면
    가이드를 읽지 않은 에이전트가 나머지 판매처엔 예약 링크가 없다고 여긴다.
    """
    if not url_limit or not any(offer.url_omitted for offer in offers):
        return []
    return [f'예약 링크(url)는 앞의 운임 {url_limit}개에만 붙였습니다. 다른 판매처는 맨 위 url(원 사이트)에서 고르거나 --offer-urls 를 늘려 다시 부르세요']


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
    round_trip: Annotated[
        bool,
        Field(exclude=True, description='왕복 달력인지 (출력하지 않음)')
    ] = False

    def not_applicable_fields(self) -> frozenset[str]:
        return frozenset() if self.round_trip else frozenset({'return_date'})


def cheapest_days(days: list[CalendarDay]) -> list[CalendarDay]:
    """달력 결과 맨 위에 올릴 싼 날 (`CHEAPEST_DAY_COUNT` 일, 같은 값이면 이른 날 먼저).

    `days` 는 날짜 순이라 에이전트가 수십 개 값에서 최저가를 직접 골라야 한다. 고르다 틀리기 쉬워 싼 날을 따로 준다.
    """
    priced = [day for day in days if day.min_price_krw is not None]
    return sorted(priced, key=lambda day: (day.min_price_krw or 0, day.date))[:CHEAPEST_DAY_COUNT]


# 두 소스 달력 결과의 `cheapest_days` 필드. 설명의 날 수를 `CHEAPEST_DAY_COUNT` 에서 만든다
CheapestDays = Annotated[
    list[CalendarDay],
    Field(description=f'days 중 싼 순 앞의 {CHEAPEST_DAY_COUNT}일 (같은 값이면 이른 날 먼저)')
]


class Destination(FlightModel):
    """어디든지 결과의 목적지 하나 (`explore`)."""

    code: Annotated[
        str | None,
        Field(description='목적지 IATA 공항·도시 코드. 소스가 주지 않으면 null (스카이스캐너는 sky_code 만 준다)')
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
