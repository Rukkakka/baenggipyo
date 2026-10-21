import logging

import httpx
import pytest

from baenggipyo.core.ratelimit import (
    RateLimitedError,
    RateLimitPolicy,
)
from baenggipyo.providers.skyscanner.api.client import SkyscannerClient
from baenggipyo.providers.skyscanner.api.request.airport import (
    SkyscannerAutosuggestSearchSearchFlightV1RequestParameterModel,
)
from baenggipyo.providers.skyscanner.api.request.common import (
    SkyscannerRadarWebUnifiedSearchPollV2RequestParameterModel,
)
from baenggipyo.providers.skyscanner.service.airport import (
    resolve_places,
    search_airports,
)
from baenggipyo.providers.skyscanner.service.common import (
    SkyscannerLeg,
    SkyscannerPlace,
)
from baenggipyo.providers.skyscanner.service.flight import (
    SkyscannerFlightConditions,
    flight_id,
    get_flight,
    parse_flight_id,
    search_flights,
)


# --- 봇 차단 ---

def test_bot_block_sets_cooldown_and_next_call_never_reaches_server() -> None:
    # 실제 차단을 다시 받으면 안 되므로 가짜 응답으로 본다. 실제 차단 응답과 같은 모양이다
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(403, json={'reason': 'blocked', 'redirect_to': '/sttc/px/captcha-v2/index.html?url=Lw=='})

    client = SkyscannerClient(rate_limit=RateLimitPolicy(min_interval=0, cooldown=1800))
    real_client = client._client
    client.__dict__['_client'] = httpx.Client(base_url=real_client.base_url, transport=httpx.MockTransport(handler), event_hooks=real_client.event_hooks)
    real_client.close()
    parameter = SkyscannerAutosuggestSearchSearchFlightV1RequestParameterModel(query='ICN')
    with client:
        with pytest.raises(RateLimitedError) as exc:
            client.run_request_autosuggest_search_search_flight_v1(parameter)
        assert exc.value.reason == 'blocked' and exc.value.retry_after > 1700
        with pytest.raises(RateLimitedError):
            client.run_request_autosuggest_search_search_flight_v1(parameter)
    assert len(sent) == 1


# --- 공항 해석 ---

def test_city_candidate_with_empty_city_id_is_one_city() -> None:
    # 도시 후보가 CityId 가 빈 GENERAL_SEARCH_V2 후보로도 온다 (2026-10-12 실측 원본에서 쓰는 필드만 남겼다). '부산' 은 그것만 와서 PlaceId == CityId 로만
    # 보면 공항 PUSA 가 되고, '서울' 은 보통 도시 후보 뒤에 같은 엔티티로 두 번 더 와서 도시로만 보면 서울이 세 곳(AIRPORT_AMBIGUOUS)이 된다
    def place(place_id: str, name: str, iata: str, city_id: str, geo_id: str, container_id: str) -> dict[str, str]:
        return {'PlaceId': place_id, 'PlaceName': name, 'IataCode': iata, 'CityId': city_id, 'CityName': name.split()[0], 'CountryId': 'KR',
                'GeoId': geo_id, 'GeoContainerId': container_id}

    seoul = place('SELA', '서울', 'SEL', '', '27538638', '27538638')
    bodies = {
        '부산': [place('PUS', '부산 김해', '', 'PUSA', '128667080', '27546072'), place('PUSA', '부산', 'PUS', '', '27546072', '27546072')],
        '서울': [{**seoul, 'CityId': 'SELA'}, place('GMP', '서울 김포', '', 'SELA', '128668604', '27538638'), seoul, seoul],
    }
    client = SkyscannerClient(rate_limit=RateLimitPolicy(min_interval=0))
    client.__dict__['_client'] = httpx.Client(base_url=client.base_url, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=bodies[request.url.path.rsplit('/', 1)[1]])
    ))
    with client:
        found = {query: [(airport.code, airport.type) for airport in search_airports(client, query, limit=10).airports] for query in bodies}
        resolved = {query: resolve_places(client, query)[0] for query in bodies}
    assert found == {'부산': [('PUS', '공항'), ('PUS', '도시')], '서울': [('SEL', '도시'), ('GMP', '공항')]}
    assert [(city.type, city.sky_id) for city in resolved.values()] == [('city', 'PUSA'), ('city', 'SELA')]


# --- 항공권 ID ---

def test_flight_id_round_trips_round_trip_itinerary() -> None:
    # 왕복 항공권 ID 는 두 여정을 | 로 잇는다 (2026-10-11 실측). 따옴표 없이 셸에 넣어도 파이프가 되지 않게 baenggipyo ID 에는 | 가 없어야 한다.
    # 여정 하나의 판매 항공사가 여럿이면 항공사 코드를 , 로 잇는다 (ICN→LHR 202건 중 79건, 2026-10-11 실측)
    seoul = SkyscannerPlace(code='SEL', type='city', sky_id='SELA', entity_id='27538638')
    narita = SkyscannerPlace(code='NRT', type='airport', sky_id='NRT', entity_id='128668889')
    legs = [SkyscannerLeg(origin=seoul, destination=narita, date='2026-11-15'), SkyscannerLeg(origin=narita, destination=seoul, date='2026-11-20')]
    itinerary_id = '12409-2611150810--31158,-32514-1-14788-2611151025|14788-2611201130--31158-0-12409-2611201400'
    conditions = SkyscannerFlightConditions(adults=2, infants=1, cabin='비즈니스석')
    value = flight_id(legs, conditions, itinerary_id)
    assert '|' not in value
    parsed_legs, parsed_conditions, parsed_id = parse_flight_id(value)
    assert [(leg.origin.entity_id, leg.origin.sky_id, leg.destination.sky_id, leg.date) for leg in parsed_legs] == [
        ('27538638', 'SELA', 'NRT', '2026-11-15'),
        ('128668889', 'NRT', 'SELA', '2026-11-20'),
    ]
    assert (parsed_conditions.adults, parsed_conditions.infants, parsed_conditions.cabin) == (2, 1, '비즈니스석')
    assert parsed_id == itinerary_id


# --- 검색 폴링 ---

def test_search_returns_received_results_when_next_poll_hits_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # 시간당 상한은 검색 도중에 닿기 쉽다. 다음 폴링이 로컬 요청 제한에 걸리면 이미 받은 결과를 버리지 않고 notes 와 함께 돌려준다.
    # 실제 API 로 맞추려면 요청 예산을 일부러 써야 해서 가짜 응답으로 본다
    monkeypatch.setattr('baenggipyo.providers.skyscanner.service.common.POLL_WAITS', (0.0,))
    sent: list[httpx.Request] = []
    body = {
        'context': {'status': 'incomplete', 'sessionId': 's'},
        'itineraries': {'results': [{
            'id': 'X',
            'price': {'raw': 175500.0, 'pricingOptionId': 'p'},
            'legs': [{'stopCount': 0, 'segments': [{'origin': {'displayCode': 'ICN'}, 'destination': {'displayCode': 'NRT'}}]}],
            'pricingOptions': [{'pricingOptionId': 'p', 'agentIds': ['a'], 'price': {'amount': 175500.0}}],
        }], 'agents': []},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=body)

    # 분당 1회라 검색 POST 뒤 폴링은 60초를 기다려야 하고, 기다릴 수 있는 시간(max_wait)은 0초다
    client = SkyscannerClient(rate_limit=RateLimitPolicy(min_interval=0, max_per_minute=1, max_wait=0))
    client.__dict__['_client'] = httpx.Client(base_url=client.base_url, transport=httpx.MockTransport(handler), event_hooks=client.rate_limit_hooks())
    incheon = SkyscannerPlace(code='ICN', type='airport', sky_id='ICN', entity_id='95673659')
    narita = SkyscannerPlace(code='NRT', type='airport', sky_id='NRT', entity_id='128668889')
    with client:
        result = search_flights(client, [SkyscannerLeg(origin=incheon, destination=narita, date='2026-11-15')])
    assert len(sent) == 1
    assert [itinerary.total_price_krw for itinerary in result.itineraries] == [175500]
    assert any('요청 제한' in note for note in result.notes)


def test_poll_error_message_and_log_hide_session_id_in_path(caplog: pytest.LogCaptureFixture) -> None:
    # 폴링 주소 경로에 검색 세션 ID 가 들어간다. 서버 오류 메시지(에러 JSON 의 message)와 --verbose 요청 로그에는 자리표시자를 남긴
    # 엔드포인트만 싣는다. 서버 오류라 실제 API 로 재현할 수 없어 가짜 응답으로 본다 (404 는 재시도하지 않아 바로 끝난다)
    # CLI 의 configure_logging 처럼 httpx 자체 요청 로그는 끈다. set_level 은 캡처 핸들러 레벨도 바꾸므로 INFO 를 나중에 건다
    caplog.set_level(logging.WARNING, logger='httpx')
    caplog.set_level(logging.INFO)
    client = SkyscannerClient(rate_limit=RateLimitPolicy(enabled=False))
    client.__dict__['_client'] = httpx.Client(
        base_url=client.base_url, transport=httpx.MockTransport(lambda request: httpx.Response(404)), event_hooks=client.rate_limit_hooks(),
    )
    with client, pytest.raises(httpx.HTTPStatusError) as exc:
        client.run_request_radar_web_unified_search_poll_v2(SkyscannerRadarWebUnifiedSearchPollV2RequestParameterModel(session_id='secret-session'))
    assert 'secret-session' not in str(exc.value) + caplog.text
    assert '/web-unified-search/{session_id}' in str(exc.value)
    assert '/web-unified-search/{session_id} -> 404' in caplog.text


# --- 판매 불가 운임 ---

def test_search_drops_itinerary_whose_offers_are_all_unpriced() -> None:
    # 판매처 운임이 모두 가격 없음(unpricedType, 0원)이면 살 수 있는 가격이 없어 목록에서 뺀다. 가격순 맨 앞에 0원으로 오면 안 된다.
    # 실측(ICN→BCN 203건, 2026-10-11)에는 없던 응답이라 가짜 응답으로 본다
    def itinerary(itinerary_id: str, amount: float, unpriced_type: str | None) -> dict[str, object]:
        return {
            'id': itinerary_id,
            'price': {'raw': amount, 'pricingOptionId': 'p'},
            'legs': [{'stopCount': 0, 'segments': [{'origin': {'displayCode': 'ICN'}, 'destination': {'displayCode': 'NRT'}}]}],
            'pricingOptions': [{'pricingOptionId': 'p', 'agentIds': ['a'], 'price': {'amount': amount}, 'unpricedType': unpriced_type}],
        }

    body = {
        'context': {'status': 'complete', 'sessionId': 's'},
        'itineraries': {'results': [itinerary('PRICED', 175500.0, None), itinerary('UNPRICED', 0.0, 'itinerary_not_returned')], 'agents': []},
    }
    client = SkyscannerClient(rate_limit=RateLimitPolicy(min_interval=0))
    client.__dict__['_client'] = httpx.Client(base_url=client.base_url, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    incheon = SkyscannerPlace(code='ICN', type='airport', sky_id='ICN', entity_id='95673659')
    narita = SkyscannerPlace(code='NRT', type='airport', sky_id='NRT', entity_id='128668889')
    with client:
        result = search_flights(client, [SkyscannerLeg(origin=incheon, destination=narita, date='2026-11-15')])
    assert [itinerary.id.rsplit('~', 1)[1] for itinerary in result.itineraries] == ['PRICED']
    assert any('가격 없음' in note for note in result.notes)


# --- 판매처별 가격 ---

def test_flight_limit_never_splits_multi_seller_combo() -> None:
    # 판매처 여러 곳에서 따로 예약하는 조합은 예약 단위마다 한 줄이라, --limit 이 줄을 세면 한 구간만 남아 나머지를 예약할 수 없다.
    # 조합이 가장 싸면(대표 가격) 맨 앞에 와서 --limit 에 잘리지 않아야 한다.
    # 조합과 그것을 가르는 limit 이 겹쳐야 나오는 드문 경우라 가짜 응답으로 본다 (실측은 2026-10-12 인천↔도쿄 왕복 응답으로 한 번 했다)
    def segment(segment_id: str, origin: str, destination: str) -> dict[str, object]:
        return {'id': segment_id, 'origin': {'displayCode': origin}, 'destination': {'displayCode': destination}}

    def item(agent_id: str, amount: float, segment_ids: list[str]) -> dict[str, object]:
        return {'agentId': agent_id, 'url': f'/{agent_id}', 'price': {'amount': amount}, 'segmentIds': segment_ids}

    whole = [
        {'pricingOptionId': agent_id, 'agentIds': [agent_id], 'price': {'amount': amount}, 'items': [item(agent_id, amount, ['s1', 's2'])]}
        for agent_id, amount in (('a', 200000.0), ('b', 210000.0))
    ]
    combo = {'pricingOptionId': 'c', 'agentIds': ['ze', 'tw'], 'price': {'amount': 150000.0},
             'items': [item('ze', 70000.0, ['s1']), item('tw', 80000.0, ['s2'])]}
    body = {
        'context': {'status': 'complete', 'sessionId': 's'},
        'itineraries': {
            'results': [{'id': 'X', 'price': {'raw': 150000.0, 'pricingOptionId': 'c'}, 'pricingOptions': [*whole, combo], 'legs': [
                {'stopCount': 0, 'segments': [segment('s1', 'PUS', 'FUK')]}, {'stopCount': 0, 'segments': [segment('s2', 'FUK', 'PUS')]},
            ]}],
            'agents': [{'id': agent_id, 'name': agent_id} for agent_id in ('a', 'b', 'ze', 'tw')],
        },
    }
    busan = SkyscannerPlace(code='PUS', type='airport', sky_id='PUS', entity_id='1')
    fukuoka = SkyscannerPlace(code='FUK', type='airport', sky_id='FUK', entity_id='2')
    legs = [SkyscannerLeg(origin=busan, destination=fukuoka, date='2026-11-15'), SkyscannerLeg(origin=fukuoka, destination=busan, date='2026-11-18')]
    value = flight_id(legs, SkyscannerFlightConditions(), 'X')
    sellers: dict[int, list[str | None]] = {}
    for limit in (1, 2):
        client = SkyscannerClient(rate_limit=RateLimitPolicy(min_interval=0))
        client.__dict__['_client'] = httpx.Client(base_url=client.base_url, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
        with client:
            detail, _ = get_flight(client, value, offer_limit=limit)
        sellers[limit] = [offer.seller for offer in detail.offers]
    assert sellers == {1: ['ze', 'tw'], 2: ['ze', 'tw', 'a']}
    assert detail.cheapest_seller == 'ze + tw'
