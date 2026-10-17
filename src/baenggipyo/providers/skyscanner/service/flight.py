"""항공권 검색과 판매처별 가격 (`search flights`, `flight`)."""

import re

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from baenggipyo.core.errors import (
    BaenggipyoError,
    fare_missing_error,
)
from baenggipyo.core.flight import (
    FlightConditions,
    Itinerary,
    Journey,
    Offer,
    SearchLeg,
    SearchSummary,
    Segment,
    shown_offers,
)
from baenggipyo.core.schema import built_from_response
from baenggipyo.providers.skyscanner.api.client import SkyscannerClient
from baenggipyo.providers.skyscanner.api.request.common import (
    SkyscannerDateModel,
    SkyscannerPlaceModel,
    SkyscannerRadarWebUnifiedSearchV2RequestPayloadModel,
    SkyscannerSearchLegModel,
)
from baenggipyo.providers.skyscanner.api.response.common import (
    SkyscannerAgentModel,
    SkyscannerItineraryModel,
    SkyscannerLegModel,
    SkyscannerPricingOptionModel,
    SkyscannerSegmentModel,
)
from baenggipyo.providers.skyscanner.service.common import (
    CABIN_BY_LETTER,
    CABIN_CLASS_BY_LABEL,
    CABIN_LETTER_BY_LABEL,
    SITE_URL,
    SkyscannerLeg,
    SkyscannerPlace,
    child_ages,
    leg_trip_type,
    results_url,
    run_search,
    stopped_early,
)

from typing import (
    Annotated,
    Literal,
)


SkyscannerFlightSort = Literal['가격', '출발시각', '소요시간']
FLIGHT_SORTS: tuple[SkyscannerFlightSort, ...] = ('가격', '출발시각', '소요시간')
DEFAULT_LIMIT: int = 10
MAX_LIMIT: int = 50
# 예약 딥링크가 하나에 1~2KB 라 다 내보내면 출력이 길다
DEFAULT_OFFER_LIMIT: int = 10
MAX_OFFER_LIMIT: int = 100

PLACE_ID_PATTERN: str = r'\d+\.[A-Z0-9]{3,5}'
LEG_ID_PATTERN: str = rf'{PLACE_ID_PATTERN}\.{PLACE_ID_PATTERN}\.\d{{8}}'
# 스카이스캐너는 왕복·다구간 항공권 ID 의 여정 ID 를 '|' 로 잇는다. 따옴표 없이 셸에 넣으면 파이프가 되므로 baenggipyo ID 에는
# '_' 로 바꿔 담고 되돌린다 (스카이스캐너 ID 에는 '_' 가 없다). 예전 ID 의 '|' 도 받는다.
# 여정 하나의 판매 항공사가 여럿이면 항공사 코드를 ',' 로 잇는다 (`12409-2611152320--31347,-32514-1-13554-2611161900`, ICN→LHR 202건 중
# 79건, 2026-10-11). 쉼표는 셸에서 따옴표 없이 넣어도 그대로라 바꾸지 않는다
SKYSCANNER_JOURNEY_SEPARATOR: str = '|'
JOURNEY_SEPARATOR: str = '_'
FLIGHT_ID_PATTERN: re.Pattern[str] = re.compile(
    r'^(?P<cabin>[YWCF])-(?P<adults>\d)(?P<children>\d)(?P<infants>\d)-'
    rf'(?P<legs>{LEG_ID_PATTERN}(?:_{LEG_ID_PATTERN})*)~(?P<itinerary_id>[0-9A-Za-z,|_-]+)$'
)
LEG_ID_PARTS: re.Pattern[str] = re.compile(r'(\d+)\.([A-Z0-9]{3,5})\.(\d+)\.([A-Z0-9]{3,5})\.(\d{4})(\d{2})(\d{2})')


class SkyscannerFlightConditions(FlightConditions):
    """스카이스캐너 검색 조건. 직항·항공사는 스카이스캐너가 받지 않아 받은 뒤 거른다."""


SELF_TRANSFER_WARNING: str = '표를 따로 사서 갈아타는 셀프 환승이라 연결편을 놓쳐도 보호받지 못하고 짐을 찾아 다시 부쳐야 할 수 있습니다'


class SkyscannerItinerary(Itinerary):
    """스카이스캐너 항공권 한 건. 셀프 환승(`isSelfTransfer`)이면 표시한다."""

    self_transfer: Annotated[
        bool | None,
        Field(description=f'셀프 환승인지: {SELF_TRANSFER_WARNING}. 셀프 환승일 때만 키가 있다 (스카이스캐너가 알려 주지 않으면 null)')
    ] = None

    def not_applicable_fields(self) -> frozenset[str]:
        fields = super().not_applicable_fields()
        return fields | {'self_transfer'} if self.self_transfer is False else fields


class SkyscannerItineraryDetail(SkyscannerItinerary):
    """`flight` 결과: 항공권 한 건과 판매처별 가격."""

    offers: Annotated[
        list[Offer],
        Field(description='판매처별 가격 (싼 순)')
    ]


class SkyscannerSearchSummary(SearchSummary):
    """어떤 조건으로 찾았는지. 스카이스캐너가 준 항공권 수를 함께 준다."""

    total_count: Annotated[
        int,
        Field(description='스카이스캐너가 준 항공권 수 (직항·항공사 조건을 걸기 전)')
    ]


class SkyscannerFlightSearchResult(BaseModel):
    """`search flights` 결과."""

    model_config = ConfigDict(
        extra='forbid',
    )

    search: Annotated[
        SkyscannerSearchSummary,
        Field(description='검색 조건')
    ]
    returned: Annotated[
        int,
        Field(description='돌려준 항공권 수')
    ]
    itineraries: Annotated[
        list[SkyscannerItinerary],
        Field(description='항공권')
    ]
    notes: Annotated[
        list[str],
        Field(exclude=True, description='결과 안내')
    ] = []


def flight_id(legs: list[SkyscannerLeg], conditions: SkyscannerFlightConditions, itinerary_id: str) -> str:
    """`flight` 에 넘길 항공권 ID. 같은 조건으로 다시 검색해 찾으므로 조건을 함께 담는다.

    형식: `<좌석>-<성인><소아><유아>-<구간>[_<구간>…]~<항공권 ID>`, 구간은 `<출발 엔티티>.<출발 스카이 ID>.<도착 엔티티>.<도착 스카이 ID>.<YYYYMMDD>`.
    스카이스캐너 항공권 ID 는 장소·시각·항공사로 만든 값이라 다시 검색해도 같다. 여정 사이의 '|' 는 셸에서 안전한 '_' 로 바꾼다.
    """
    leg_ids = '_'.join(
        f'{leg.origin.entity_id}.{leg.origin.sky_id}.{leg.destination.entity_id}.{leg.destination.sky_id}.{leg.date.replace("-", "")}'
        for leg in legs
    )
    letter = CABIN_LETTER_BY_LABEL[conditions.cabin]
    shell_safe_id = itinerary_id.replace(SKYSCANNER_JOURNEY_SEPARATOR, JOURNEY_SEPARATOR)
    return f'{letter}-{conditions.adults}{conditions.children}{conditions.infants}-{leg_ids}~{shell_safe_id}'


def parse_flight_id(value: str) -> tuple[list[SkyscannerLeg], SkyscannerFlightConditions, str]:
    """`flight_id` 를 되돌린다.

    Raises:
        BaenggipyoError: 형식이 다를 때 (`INVALID_ARGUMENT`).
    """
    match = FLIGHT_ID_PATTERN.fullmatch(value.strip())
    if match is None:
        raise BaenggipyoError(
            code='INVALID_ARGUMENT',
            message=f'스카이스캐너 항공권 ID 형식이 아닙니다: {value}',
            hint='skyscanner search flights 결과의 id 를 그대로 넣으세요',
        )
    legs = []
    for origin_entity, origin_sky, destination_entity, destination_sky, year, month, day in LEG_ID_PARTS.findall(match['legs']):
        legs.append(SkyscannerLeg(
            origin=_place_from_id(origin_entity, origin_sky),
            destination=_place_from_id(destination_entity, destination_sky),
            date=f'{year}-{month}-{day}',
        ))
    conditions = SkyscannerFlightConditions(
        adults=int(match['adults']),
        children=int(match['children']),
        infants=int(match['infants']),
        cabin=CABIN_BY_LETTER[match['cabin']],
    )
    return legs, conditions, match['itinerary_id'].replace(JOURNEY_SEPARATOR, SKYSCANNER_JOURNEY_SEPARATOR)


def _place_from_id(entity_id: str, sky_id: str) -> SkyscannerPlace:
    # 공항 스카이 ID 는 IATA 코드(3자)다. 도시는 4자 이상이고 IATA 로 되돌릴 수 없는 것도 있어(런던 LOND) 스카이 ID 를 그대로 코드로 쓴다
    return SkyscannerPlace(code=sky_id, type='airport' if len(sky_id) == 3 else 'city', sky_id=sky_id, entity_id=entity_id)


def _search_payload(legs: list[SkyscannerLeg], conditions: SkyscannerFlightConditions) -> SkyscannerRadarWebUnifiedSearchV2RequestPayloadModel:
    return SkyscannerRadarWebUnifiedSearchV2RequestPayloadModel(
        cabin_class=CABIN_CLASS_BY_LABEL[conditions.cabin],
        child_ages=child_ages(conditions.children, conditions.infants),
        adults=conditions.adults,
        legs=[
            SkyscannerSearchLegModel(
                leg_origin=SkyscannerPlaceModel(type='entity', entity_id=leg.origin.entity_id),
                leg_destination=SkyscannerPlaceModel(type='entity', entity_id=leg.destination.entity_id),
                dates=SkyscannerDateModel(type='date', year=leg.date[:4], month=leg.date[5:7], day=leg.date[8:]),
            )
            for leg in legs
        ],
    )


def _local_minute(value: str | None) -> str | None:
    # 스카이스캐너는 YYYY-MM-DDTHH:MM:SS 로 준다. 출력 규칙(YYYY-MM-DDTHH:MM)에 맞춰 초를 뗀다
    return value[:16] if value else None


def _segment(segment: SkyscannerSegmentModel) -> Segment:
    marketing = segment.marketing_carrier
    operating = segment.operating_carrier
    # 모르는 코드는 빈 문자열이 아니라 None(모름)으로 둔다. 코드나 편 번호 중 하나라도 없으면 편명도 모른다
    code = (marketing.display_code or marketing.alternate_id or None) if marketing else None
    return Segment(
        airline_code=code,
        airline=marketing.name if marketing else None,
        flight_number=f'{code}{segment.flight_number}' if code and segment.flight_number else None,
        operating_airline_code=(operating.display_code or operating.alternate_id or None) if operating else None,
        operating_airline=operating.name if operating else None,
        departure_airport=segment.origin.display_code or None,
        arrival_airport=segment.destination.display_code or None,
        departure_at=_local_minute(segment.departure),
        arrival_at=_local_minute(segment.arrival),
        duration_minutes=segment.duration_in_minutes,
    )


def _journey(leg: SkyscannerLegModel) -> Journey:
    segments = [_segment(segment) for segment in leg.segments]
    return Journey(
        departure_airport=segments[0].departure_airport,
        arrival_airport=segments[-1].arrival_airport,
        departure_at=_local_minute(leg.departure),
        arrival_at=_local_minute(leg.arrival),
        duration_minutes=leg.duration_in_minutes,
        stops=leg.stop_count if leg.stop_count is not None else len(segments) - 1,
        stopover_airports=[segment.arrival_airport for segment in segments[:-1]],
        segments=segments,
    )


def _priced_options(result: SkyscannerItineraryModel) -> list[SkyscannerPricingOptionModel]:
    """가격을 준 판매처 운임. 가격 없음 표시(`unpricedType`)가 있는 운임은 가격이 0 으로 와서 살 수 있는 값이 아니다."""
    return [option for option in result.pricing_options if option.unpriced_type is None]


def _cheapest_option(result: SkyscannerItineraryModel) -> SkyscannerPricingOptionModel | None:
    """대표 가격(`price`)의 판매처 운임. 운임 ID 로만 찾는다.

    대표 가격에 운임 ID 가 없으면 어느 운임인지 모르므로 None 이다. ID 가 없는 운임끼리 None == None 으로 짝지으면 다른 판매처를 대표 판매처로 낸다.
    """
    option_id = result.price.pricing_option_id if result.price else None
    if option_id is None:
        return None
    return next((option for option in result.pricing_options if option.pricing_option_id == option_id), None)


def _itinerary_fields(
    result: SkyscannerItineraryModel,
    agents: dict[str, SkyscannerAgentModel],
    *,
    legs: list[SkyscannerLeg],
    conditions: SkyscannerFlightConditions,
) -> dict[str, object]:
    total = round(result.price.raw) if result.price and result.price.raw is not None else None
    cheapest = _cheapest_option(result)
    # 따로 예약하는 조합이면 판매처가 여럿이다
    sellers = [agents[agent_id].name for agent_id in cheapest.agent_ids if agent_id in agents] if cheapest else []
    return {
        'source': 'skyscanner',
        'id': flight_id(legs, conditions, result.id),
        'url': results_url(legs, adults=conditions.adults, children=conditions.children, infants=conditions.infants, cabin=conditions.cabin),
        'trip_type': leg_trip_type(legs),
        'cabin': conditions.cabin,
        'journeys': [_journey(leg) for leg in result.legs],
        'total_price_krw': total,
        # 스카이스캐너 가격은 모든 승객 합계다. 성인 1명만 찾았을 때만 1인 요금과 같다 (나눗셈으로 만들지 않는다)
        'price_per_adult_krw': total if conditions.only_one_adult else None,
        # 한 판매처 운임은 판매처마다 하나라 가격을 준 운임 수가 판매처 수다 (ICN→BCN 203건 모두 같았다, 2026-10-11).
        # 판매처 여러 곳에서 따로 예약하는 조합은 운임 하나로 센다
        'offer_count': len(_priced_options(result)),
        'cheapest_seller': ' + '.join(name for name in sellers if name) or None,
        'baggage_included': None,
        'self_transfer': result.is_self_transfer,
    }


def _has_route(result: SkyscannerItineraryModel) -> bool:
    """여정·구간이 있어 보여 줄 수 있는 항공권인지."""
    return bool(result.legs) and all(leg.segments for leg in result.legs)


def _carrier_code(segment: SkyscannerSegmentModel) -> str | None:
    return segment.marketing_carrier.display_code if segment.marketing_carrier else None


def _verifiable(result: SkyscannerItineraryModel, conditions: SkyscannerFlightConditions) -> bool:
    """서버가 받지 않는 조건(직항·항공사)을 판정할 값(경유 수·판매 항공사 코드)이 응답에 있는지."""
    if conditions.direct and any(leg.stop_count is None for leg in result.legs):
        return False
    return not conditions.airlines or all(_carrier_code(segment) for leg in result.legs for segment in leg.segments)


def _matches(result: SkyscannerItineraryModel, conditions: SkyscannerFlightConditions) -> bool:
    """직항·항공사 조건을 응답 값으로 판정한다. 판정할 값이 있는 항공권(`_verifiable`)만 넣는다."""
    if conditions.direct and any(leg.stop_count != 0 for leg in result.legs):
        return False
    return not conditions.airlines or all(_carrier_code(segment) in conditions.airlines for leg in result.legs for segment in leg.segments)


def _sort_key(sort: SkyscannerFlightSort, result: SkyscannerItineraryModel) -> tuple[bool, object]:
    """원본 값으로 세운다. 출력 모델로 바꾼 값(`_itinerary_fields`)과 같은 값이라, 세운 뒤 앞의 limit 개만 바꾼다.

    결과 전부를 바꾼 뒤 세우면 큰 검색에서 낭비가 크다 (8,484건 변환 0.8초, 2026-10-11 측정).
    """
    if sort == '출발시각':
        value: object = _local_minute(result.legs[0].departure) if result.legs else None
    elif sort == '소요시간':
        durations = [leg.duration_in_minutes for leg in result.legs]
        value = None if not durations or any(duration is None for duration in durations) else sum(d or 0 for d in durations)
    else:
        value = round(result.price.raw) if result.price and result.price.raw is not None else None
    return value is None, value if value is not None else 0


def search_flights(
    client: SkyscannerClient,
    legs: list[SkyscannerLeg],
    *,
    conditions: SkyscannerFlightConditions | None = None,
    sort: SkyscannerFlightSort = '가격',
    limit: int = DEFAULT_LIMIT,
) -> SkyscannerFlightSearchResult:
    """편도·왕복·다구간 항공권을 찾는다 (검색 1회 + 폴링, 보통 2~10초).

    스카이스캐너는 폴링이 끝난 결과를 한 번에 주고 직항·항공사 조건을 받지 않는다. 그래서 받은 결과에서 조건을 거르고, 정한 순서로
    세운 뒤 자른다 (서버 필터 없는 대체 조회).
    """
    conditions = conditions or SkyscannerFlightConditions()
    # 엔티티 ID 는 자동완성 응답 값이라 검증 실패는 입력 오류가 아니다 (flight 는 ID 에서 만들어 입력 오류가 맞으니 감싸지 않는다)
    with built_from_response('스카이스캐너 항공권 검색'):
        payload = _search_payload(legs, conditions)
    response, completed, limited = run_search(client, payload)
    found = response.itineraries.results if response.itineraries else []
    agents = {agent.id: agent for agent in response.itineraries.agents} if response.itineraries else {}
    routed = [result for result in found if _has_route(result)]
    # 판매처 운임이 모두 가격 없음(unpricedType, 0원)이면 살 수 있는 가격이 없다. 가격순 맨 앞에 0원으로 오지 않게 뺀다
    # (ICN→BCN 203건 중 0건, 2026-10-11. 일부 운임만 가격 없음인 항공권은 36건)
    priced = [result for result in routed if _priced_options(result)]
    verifiable = [result for result in priced if _verifiable(result, conditions)]
    matched = sorted((result for result in verifiable if _matches(result, conditions)), key=lambda result: _sort_key(sort, result))
    notes = []
    if not completed:
        notes.append(f'{stopped_early(limited)} 받은 데까지만 보여 줍니다. 더 싼 항공권이 빠졌을 수 있습니다')
    if len(routed) < len(found):
        notes.append(f'여정 정보가 응답에 없는 항공권 {len(found) - len(routed)}개는 뺐습니다')
    if len(priced) < len(routed):
        notes.append(f'판매처 운임이 모두 가격 없음으로 온 항공권 {len(routed) - len(priced)}개는 뺐습니다')
    filters = [label for label, on in (('직항', conditions.direct), ('항공사', bool(conditions.airlines))) if on]
    if filters:
        notes.append(f"{'·'.join(filters)} 조건은 스카이스캐너가 받지 않아 받은 {len(found)}개에서 걸렀습니다 ({len(matched)}개 남음)")
    if len(verifiable) < len(priced):
        notes.append(f"{'·'.join(filters)} 여부를 판정할 값(경유 수·판매 항공사)이 응답에 없는 항공권 {len(priced) - len(verifiable)}개도 뺐습니다")
    notes.append(f'{sort} 순으로 baenggipyo 가 세웠습니다 (스카이스캐너 기본 순서는 추천순)')
    if len(matched) > limit:
        notes.append(f'조건에 맞는 항공권 {len(matched)}개 중 앞의 {limit}개만 보여 줍니다 (--limit 최대 {MAX_LIMIT})')
    if not conditions.only_one_adult:
        notes.append('스카이스캐너는 모든 승객 합계만 주므로 price_per_adult_krw 는 null 입니다')
    summary = SkyscannerSearchSummary(
        trip_type=leg_trip_type(legs),
        legs=[SearchLeg(origin=leg.origin.code, destination=leg.destination.code, date=leg.date) for leg in legs],
        cabin=conditions.cabin,
        adults=conditions.adults,
        children=conditions.children,
        infants=conditions.infants,
        direct=conditions.direct,
        total_count=len(found),
        url=results_url(legs, adults=conditions.adults, children=conditions.children, infants=conditions.infants, cabin=conditions.cabin),
    )
    shown = [SkyscannerItinerary.model_validate(_itinerary_fields(result, agents, legs=legs, conditions=conditions)) for result in matched[:limit]]
    self_transfers = sum(1 for itinerary in shown if itinerary.self_transfer)
    if self_transfers:
        notes.append(f'self_transfer 가 true 인 항공권 {self_transfers}개는 {SELF_TRANSFER_WARNING}')
    return SkyscannerFlightSearchResult(search=summary, returned=len(shown), itineraries=shown, notes=notes)


def _deeplink(url: str | None) -> str | None:
    # 예약 딥링크는 사이트 기준 상대 경로로 온다 (실측 4,693개 모두, 2026-10-11). 절대 주소로 오면 그대로 쓴다
    if not url:
        return None
    return url if url.startswith(('https://', 'http://')) else f'{SITE_URL}{url}'


def _part_label(result: SkyscannerItineraryModel, segment_ids: list[str]) -> str:
    """예약 단위가 사는 구간 (`PUS→FUK`). 구간 ID 를 찾지 못하면 '일부 구간'."""
    segments = {segment.id: segment for leg in result.legs for segment in leg.segments if segment.id}
    routes = [
        f'{segments[segment_id].origin.display_code}→{segments[segment_id].destination.display_code}'
        for segment_id in segment_ids if segment_id in segments
    ]
    return ', '.join(routes) or '일부 구간'


def _offers(
    result: SkyscannerItineraryModel,
    agents: dict[str, SkyscannerAgentModel],
    conditions: SkyscannerFlightConditions,
) -> list[list[Offer]]:
    """판매처 운임별 `offers` 줄 묶음을 운임 가격이 싼 순으로. 판매처 여러 곳에서 따로 예약하는 조합은 조합 합계로 함께 세운다.

    조합을 한 판매처 운임 뒤에 따로 두면, 조합이 가장 쌀 때 `--limit` 에 잘려 대표 가격(`total_price_krw`·`cheapest_seller`)의 운임이 `offers` 에 없다.
    가격이 같으면 한 판매처 운임을 앞에 둔다. 조합은 예약 단위마다 한 줄이다 (판매처·가격·링크가 단위마다 따로다). 한 줄로 내면 다른 구간의 예약 링크가 빠진다. 그래서 운임 하나가
    여러 줄일 수 있고, `--limit` 은 줄이 아니라 이 묶음으로 세어 조합의 줄을 가르지 않는다 (한 줄만 남으면 나머지 구간을 예약할 수 없다).
    """
    def offer(agent_id: str | None, amount: float | None, url: str | None, fare_name: str | None = None) -> Offer:
        agent = agents.get(agent_id) if agent_id else None
        total = round(amount) if amount is not None else None
        return Offer(
            seller=agent.name if agent else None,
            seller_type=('항공사' if agent.is_carrier else '여행사') if agent and agent.is_carrier is not None else None,
            total_price_krw=total,
            price_per_adult_krw=total if conditions.only_one_adult else None,
            fare_name=fare_name,
            url=_deeplink(url),
        )

    groups: list[tuple[float | None, bool, list[Offer]]] = []
    for option in _priced_options(result):
        amount = option.price.amount if option.price else None
        if len(option.items) <= 1:
            first_item = option.items[0] if option.items else None
            groups.append((amount, False, [offer(option.agent_ids[0] if option.agent_ids else None, amount, first_item.url if first_item else None)]))
            continue
        names = [(agents[item.agent_id].name if item.agent_id in agents else None) or '다른 판매처' for item in option.items]
        rows = [
            offer(
                item.agent_id,
                item.price.amount if item.price else None,
                item.url,
                fare_name=f"{_part_label(result, item.segment_ids)}만 (나머지는 {', '.join(n for j, n in enumerate(names) if j != i)}에서 따로 예약)",
            )
            for i, item in enumerate(option.items)
        ]
        groups.append((amount, True, rows))
    groups.sort(key=lambda group: (group[0] is None, group[0] or 0, group[1]))
    return [rows for _, _, rows in groups]


def get_flight(
    client: SkyscannerClient,
    value: str,
    *,
    offer_limit: int = DEFAULT_OFFER_LIMIT,
    url_limit: int | None = None,
) -> tuple[SkyscannerItineraryDetail, list[str]]:
    """항공권 하나의 판매처별 가격과 예약 링크. 같은 조건으로 다시 검색해 찾는다 (검색 1회 + 폴링).

    `url_limit` 을 주면 앞의 그만큼 운임에만 예약 링크를 붙인다 (`shown_offers`).

    Raises:
        FareUnavailableError: 검색이 끝났는데 그 항공권이 없을 때 (매진·운항 변경).
        SearchIncompleteError: 검색이 시간 상한 안에 끝나지 않아 그 항공권을 아직 받지 못했을 때 (`UPSTREAM_ERROR`, `reason: SEARCH_INCOMPLETE`).
        RateLimitedError: 검색 폴링이 요청 제한에 걸려 멈췄는데 그 항공권을 아직 받지 못했을 때.
    """
    legs, conditions, itinerary_id = parse_flight_id(value)
    response, completed, limited = run_search(client, _search_payload(legs, conditions))
    found = response.itineraries.results if response.itineraries else []
    result = next((result for result in found if result.id == itinerary_id), None)
    # 여정·구간 정보나 가격을 준 판매처 운임이 없는 항공권은 search flights 도 빼므로 결과에 없는 것으로 낸다.
    # ID 에는 직항·항공사 조건이 없어 구조만 본다
    if result is None or not _has_route(result) or not _priced_options(result):
        if limited is not None:
            # 요청 제한에 걸려 멈춘 검색이라 아직 팔고 있을 수 있다. 언제 다시 부를 수 있는지 알리는 요청 제한 에러로 낸다
            raise limited
        raise fare_missing_error('skyscanner', value, cut_short=not completed, reason='NOT_IN_RESULTS', alternative='naver')
    agents = {agent.id: agent for agent in response.itineraries.agents} if response.itineraries else {}
    fares = _offers(result, agents, conditions)
    shown = shown_offers(fares, offer_limit, url_limit)
    notes = ['가격은 방금 다시 검색한 값입니다']
    if not completed:
        notes.append(f'{stopped_early(limited)}, 판매처가 더 있을 수 있습니다')
    unpriced = len(result.pricing_options) - len(_priced_options(result))
    if unpriced:
        notes.append(f'스카이스캐너가 가격 없음으로 준 판매처 운임 {unpriced}개는 뺐습니다')
    if any(offer.fare_name for offer in shown):
        notes.append('판매처 여러 곳에서 따로 예약하는 조합은 조합 합계 순서에 구간마다 한 줄씩 둡니다 (줄의 가격·url 은 fare_name 의 구간만이라 같은 조합의 줄을 모두 각 url 에서 따로 예약)')
    if len(fares) > offer_limit:
        notes.append(f'판매처 운임 {len(fares)}개 중 앞의 {offer_limit}개만 보여 줍니다 (--limit 최대 {MAX_OFFER_LIMIT})')
    fields = _itinerary_fields(result, agents, legs=legs, conditions=conditions)
    detail = SkyscannerItineraryDetail.model_validate({**fields, 'offers': shown})
    if detail.self_transfer:
        notes.append(f'이 항공권은 {SELF_TRANSFER_WARNING}')
    return detail, notes
