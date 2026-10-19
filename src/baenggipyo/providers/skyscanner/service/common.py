"""두 모듈 이상이 쓰는 장소·구간 모델, 좌석 코드표, 결과 페이지 URL, 검색 폴링."""

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
    TripType,
    trip_type_of,
)
from baenggipyo.core.ratelimit import RateLimitedError
from baenggipyo.core.schema import built_from_response
from baenggipyo.providers.skyscanner.api.client import SkyscannerClient
from baenggipyo.providers.skyscanner.api.request.common import (
    SkyscannerCabinClass,
    SkyscannerRadarWebUnifiedSearchPollV2RequestParameterModel,
    SkyscannerRadarWebUnifiedSearchV2RequestPayloadModel,
)
from baenggipyo.providers.skyscanner.api.response.common import (
    SkyscannerRadarWebUnifiedSearchV2ResponseModel,
)

from typing import (
    Annotated,
    Literal,
)


SITE_URL: str = 'https://www.skyscanner.co.kr'
KOREA_COUNTRY_ID: str = 'KR'

CABIN_CLASS_BY_LABEL: dict[CabinClass, SkyscannerCabinClass] = {
    '일반석': 'ECONOMY',
    '프리미엄일반석': 'PREMIUM_ECONOMY',
    '비즈니스석': 'BUSINESS',
    '일등석': 'FIRST',
}
# 항공권 ID 에 넣는 한 글자 (IATA 예약 등급 관례)
CABIN_LETTER_BY_LABEL: dict[CabinClass, str] = {
    '일반석': 'Y',
    '프리미엄일반석': 'W',
    '비즈니스석': 'C',
    '일등석': 'F',
}
CABIN_BY_LETTER: dict[str, CabinClass] = {letter: label for label, letter in CABIN_LETTER_BY_LABEL.items()}

# 스카이스캐너는 소아·유아를 나이로 받는다. 소아(만 2~11세) 운임은 나이에 따라 달라지지 않는다고 보고 대표 나이를 넣는다
CHILD_AGE: int = 8
INFANT_AGE: int = 1

# 폴링 응답마다 그때까지의 결과 전체가 온다 (김포↔제주 왕복은 8,359건 104MB, 2026-10-11 실측). 사이트 간격(1초씩)을 따르면 큰 검색에서
# 같은 데이터를 여러 번 받으므로 간격을 늘려 받는 횟수를 줄인다. 첫 응답 뒤 60초가 지나면 사이트처럼 멈춘다
POLL_WAITS: tuple[float, ...] = (2.0, 3.0, 5.0, 8.0, 12.0)
POLL_SECONDS: float = 60.0

ESTIMATE_NOTE: str = (
    '이 가격은 실시간이 아니라 스카이스캐너가 최근 검색에서 모은 추정가입니다. 고른 날짜·목적지는 search flights 로 실제 가격을 확인하세요'
)

# run_search 가 검색 완료를 받기 전에 멈춘 까닭 중 다시 불러도 같은 것 (`PARSE_ERROR` 의 reason) → `notes` 문구 앞부분 (`stopped_early`)
STOP_PHRASE_BY_REASON: dict[str, str] = {
    'SESSION_ID_MISSING': '스카이스캐너 검색 응답에 세션 ID 가 없어 남은 결과를 묻지 못하고 멈춰',
    'SEARCH_STATUS_UNKNOWN': '스카이스캐너 검색 응답의 진행 상태를 알 수 없어 멈춰',
}


class SkyscannerPlace(BaseModel):
    """검색에 넣을 공항·도시 하나 (자동완성으로 정한 값)."""

    model_config = ConfigDict(
        extra='forbid',
    )

    code: Annotated[
        str,
        Field(description='IATA 공항·도시 코드')
    ]
    type: Annotated[
        Literal['airport', 'city'],
        Field(description='airport 또는 city')
    ]
    sky_id: Annotated[
        str,
        Field(description='스카이 ID (공항 ICN, 도시 SELA). 달력·URL 에 쓴다')
    ]
    entity_id: Annotated[
        str,
        Field(description='엔티티 ID. 검색에 쓴다')
    ]
    name: Annotated[
        str | None,
        Field(description='이름')
    ] = None
    domestic: Annotated[
        bool | None,
        Field(description='한국 국내인지')
    ] = None


class SkyscannerLeg(BaseModel):
    """여정 한 구간."""

    model_config = ConfigDict(
        extra='forbid',
    )

    origin: Annotated[
        SkyscannerPlace,
        Field(description='출발지')
    ]
    destination: Annotated[
        SkyscannerPlace,
        Field(description='도착지')
    ]
    date: Annotated[
        str,
        Field(pattern=r'^\d{4}-\d{2}-\d{2}$', description='출발 날짜 (YYYY-MM-DD)')
    ]


def leg_trip_type(legs: list[SkyscannerLeg]) -> TripType:
    return trip_type_of([(leg.origin.code, leg.destination.code) for leg in legs])


def child_ages(children: int, infants: int) -> list[int]:
    return [CHILD_AGE] * children + [INFANT_AGE] * infants


def results_url(legs: list[SkyscannerLeg], *, adults: int, children: int, infants: int, cabin: CabinClass) -> str:
    """사이트 검색 결과 페이지 URL (홈 번들의 URL 생성 규칙).

    편도·왕복은 `/transport/flights/{출발}/{도착}/{YYMMDD}[/{YYMMDD}]/`, 다구간은 `/transport/d/{출발}/{YYYY-MM-DD}/{도착}/…/`.
    """
    query: dict[str, str | int] = {
        'adultsv2': adults,
        'cabinclass': CABIN_CLASS_BY_LABEL[cabin].lower().replace('_', ''),
        'childrenv2': '|'.join(str(age) for age in child_ages(children, infants)),
    }
    trip_type = leg_trip_type(legs)
    if trip_type == '다구간':
        path = ''.join(f'/{leg.origin.sky_id.lower()}/{leg.date}/{leg.destination.sky_id.lower()}' for leg in legs)
        return f'{SITE_URL}/transport/d{path}/?{urlencode(query)}'
    first = legs[0]
    path = f'/{first.origin.sky_id.lower()}/{first.destination.sky_id.lower()}/{first.date.replace("-", "")[2:]}'
    if trip_type == '왕복':
        path += f'/{legs[1].date.replace("-", "")[2:]}'
    query['rtn'] = 1 if trip_type == '왕복' else 0
    return f'{SITE_URL}/transport/flights{path}/?{urlencode(query)}'


def poll_wait_seconds(poll_count: int) -> float:
    """n 번째 폴링 전에 기다릴 초."""
    return POLL_WAITS[min(poll_count, len(POLL_WAITS) - 1)]


def run_search(
    client: SkyscannerClient,
    payload: SkyscannerRadarWebUnifiedSearchV2RequestPayloadModel,
    poll_seconds: float = POLL_SECONDS,
) -> tuple[SkyscannerRadarWebUnifiedSearchV2ResponseModel, bool, BaenggipyoError | None]:
    """검색을 시작하고 `complete` 가 될 때까지 폴링한다 (요청 1회 + 폴링).

    완료는 응답의 `context.status` 로만 판정한다. 폴링 응답이 `sessionId` 를 빠뜨려도 같은 검색이라 처음 받은 값으로 계속 묻는다.
    세션 ID 를 한 번도 받지 못했거나(빈 문자열 포함), 진행 중(`incomplete`)도 완료(`complete`)도 아닌 응답(`context`·상태가 빠졌거나 모르는 값)이
    오면 더 묻지 못해 미완료로 끝낸다. 시간 상한에 닿은 것(잠시 뒤 다시 부르면 끝날 수 있다)과 가르도록 그 까닭을 `PARSE_ERROR` 로 함께
    돌려준다 (다시 불러도 같다).
    다음 폴링이 로컬 요청 제한(분당·시간당 상한, 쿨다운)에 걸리면 에러로 끝내지 않고 그때까지 받은 응답을 돌려준다. 이미 받은 결과와 쓴 요청을 버리지 않는다.
    폴링 요청의 슬롯을 기다리는 시간도 폴링 시간 상한에 넣어, 상한 안에 슬롯이 나지 않을 때도 같이 멈춘다. 다만 폴링 요청 자체가 실패하면
    (서버 오류 재시도가 요청 제한에 걸린 경우 포함) 앞 응답을 이미 놓아 에러로 끝난다.

    Returns:
        tuple[응답, 완료 여부, 멈춘 까닭]: 시간 상한·요청 제한에 닿았거나 완료 표시를 받지 못하면 그때까지 받은 응답과 False.
            멈춘 까닭은 요청 제한에 걸렸을 때(`RateLimitedError`, 언제 풀리는지 알릴 때 쓴다)와 세션 ID 가 없거나 진행 상태를 알 수 없어
            더 묻지 못했을 때(`PARSE_ERROR`, reason 은 `STOP_PHRASE_BY_REASON` 의 키)만 있다. 시간 상한에 닿았거나 끝까지 받았으면 None 이다.
    """
    response = client.run_request_radar_web_unified_search_v2(payload)
    # 폴링 시간은 첫 응답을 받은 뒤부터 잰다. 요청 제한 훅이 POST 앞에서 슬롯을 기다린 시간(최대 max_wait)이 폴링 시간을 깎지 않게 한다
    started = time.monotonic()
    session_id = response.context.session_id if response.context else None
    polls = 0
    while response.context is not None and response.context.status == 'incomplete':
        session_id = response.context.session_id or session_id
        if not session_id:
            return response, False, BaenggipyoError(
                code='PARSE_ERROR',
                message='스카이스캐너 검색 응답에 세션 ID 가 없어 남은 결과를 이어서 묻지 못했습니다',
                hint='비공식 API 응답 형식이 바뀌었을 수 있습니다. 다른 소스를 쓰세요',
                details={'reason': 'SESSION_ID_MISSING'},
            )
        wait = poll_wait_seconds(polls)
        if time.monotonic() - started + wait > poll_seconds:
            return response, False, None
        time.sleep(wait)
        # 세션 ID 는 검색 응답 값이라 검증 실패는 입력 오류(INVALID_ARGUMENT)가 아니다
        with built_from_response('스카이스캐너 검색 폴링'):
            parameter = SkyscannerRadarWebUnifiedSearchPollV2RequestParameterModel(session_id=session_id)
        # 큰 검색은 응답 한 벌이 수백 MB 다. 다음 응답을 받는 동안 앞 응답을 들고 있지 않게 먼저 놓는다. 놓으면 폴링이 실패할 때 받은 결과도
        # 잃으므로, 놓기 전에 폴링 요청의 슬롯을 예약해 둔다. 시간당 상한은 검색 도중에 닿기 쉽다 (검색 한 번이 요청 4~10회, 상한 30회).
        # 슬롯은 폴링 시간 상한 안의 것만 잡는다. 요청 제한 훅이 슬롯까지 기다리는 시간(최대 max_wait)이 상한 검사 뒤에 붙어 상한을 넘기지 않게 한다.
        # 예약한 뒤에 다른 프로세스가 쿨다운을 걸거나 서버가 막으면(봇 차단) 여전히 에러로 끝난다. 폴링이 서버 오류로 재시도하면 재시도는 슬롯을
        # 새로 잡으므로 그때 상한에 걸려도 에러로 끝난다 (재시도 슬롯까지 미리 잡으면 대개 쓰지 않을 슬롯이 시간당 상한을 깎는다)
        try:
            client.rate_limiter.reserve_ahead(within=max(0.0, poll_seconds - (time.monotonic() - started)))
        except RateLimitedError as exc:
            return response, False, exc
        del response
        response = client.run_request_radar_web_unified_search_poll_v2(parameter)
        polls += 1
    status = response.context.status if response.context else None
    if status == 'complete':
        return response, True, None
    # 진행 중이 아닌데 완료도 아니면 기다려도 바뀌지 않는다. 시간 상한에 멈췄다고 하면 flight 가 SEARCH_INCOMPLETE(잠시 뒤 다시)를 내고,
    # 매일 API 감시도 고장이 아닌 확인 못 함으로 보아 응답 형식이 바뀐 것을 놓친다
    return response, False, BaenggipyoError(
        code='PARSE_ERROR',
        message=f"스카이스캐너 검색 응답의 진행 상태를 알 수 없어(status: {status or '없음'}) 검색이 끝났는지 모릅니다",
        hint='비공식 API 응답 형식이 바뀌었을 수 있습니다. 다른 소스를 쓰세요',
        details={'reason': 'SEARCH_STATUS_UNKNOWN'},
    )


def stopped_early(stopped: BaenggipyoError | None) -> str:
    """검색 완료를 받기 전에 멈춘 까닭 (`notes` 문구 앞부분, `run_search` 의 멈춘 까닭). 요청 제한에 걸렸으면 언제 풀리는지 넣는다."""
    if stopped is None:
        return f'스카이스캐너가 검색 완료를 알리기 전에(시간 상한 {POLL_SECONDS:g}초) 멈춰'
    if isinstance(stopped, RateLimitedError):
        return f"스카이스캐너가 검색 완료를 알리기 전에 요청 제한에 걸려({stopped.details['retry_after_seconds']}초 뒤 풀림) 멈춰"
    # 그 밖의 까닭은 다시 불러도 같은 PARSE_ERROR 뿐이다 (run_search)
    return STOP_PHRASE_BY_REASON[stopped.details['reason']]
