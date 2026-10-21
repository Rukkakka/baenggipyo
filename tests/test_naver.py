import httpx
import pytest

from baenggipyo.providers.naver.api.client import NaverClient
from baenggipyo.providers.naver.api.request.flight import (
    NaverFlightInternationalSearchFlightsRequestPayloadModel,
)
from baenggipyo.providers.naver.api.response.domestic import (
    NaverDomesticFareModel,
    NaverDomesticFlightModel,
)
from baenggipyo.providers.naver.api.response.flight import (
    NaverBookingOptionsResponseModel,
    NaverFareModel,
    NaverFlightInternationalSearchFlightsResponseModel,
)
from baenggipyo.providers.naver.api.stream import last_event
from baenggipyo.providers.naver.service.common import (
    NaverFlightConditions,
    NaverLeg,
    NaverLocation,
)
from baenggipyo.providers.naver.service.domestic import (
    domestic_flight_id,
    fare_per_person,
    fare_total,
    parse_domestic_flight_id,
)
from baenggipyo.providers.naver.service.flight import (
    card_discount_fare,
    flight_id,
    general_fare,
    get_flight,
    parse_flight_id,
    search_flights,
)


def fare(partner: str, fare_type: str, adult: int) -> NaverFareModel:
    return NaverFareModel.model_validate({'partnerCode': partner, 'fareType': fare_type, 'adult': {'totalFare': adult}})


# --- 대표 가격 ---

def test_card_discount_is_never_the_representative_price() -> None:
    # 카드 실적 할인가가 더 싸도 대표 가격은 모든 결제수단(A01) 최저가다 (2026-10-11 결정)
    fares = [fare('P1', 'A01/B80', 100), fare('P2', 'A01', 130), fare('P3', 'A01', 120), fare('P4', 'A07', 90)]
    general = general_fare(fares)
    assert general is not None and general.partner_code == 'P3'
    discount = card_discount_fare(fares, general)
    assert discount is not None and discount.fare_type == 'A01/B80'
    # 대표 가격보다 싸지 않은 할인은 따로 내지 않는다
    assert card_discount_fare([fare('P1', 'A01/B80', 125), fare('P3', 'A01', 120)], general) is None


def test_flight_price_comes_from_listed_sellers_and_cut_search_is_noted(monkeypatch: pytest.MonkeyPatch) -> None:
    # 실시간이 아니라 offers 에서 뺀 판매처(P2)가 가장 싸도, 대표 가격은 offers 에 낸 판매처로 정한다. 그 가격으로 살 판매처가 목록에 있어야 한다.
    # 캐시 조회가 시간 상한에 끊긴 채 항공권을 찾으면 일부 판매처만 반영됐다고 알린다. 끊긴 캐시 조회는 실제 API 로 재현할 수 없어 가짜 응답으로 본다
    itinerary_id = '20261115ICNNRTZE0605'
    point = {'airportCode': 'ICN', 'dateTime': '202611150800'}
    event = NaverFlightInternationalSearchFlightsResponseModel.model_validate({
        'status': {'isCompleted': False, 'completedPartnerCount': 3, 'requestedPartnerCount': 20},
        'itineraries': [{'itineraryId': itinerary_id, 'segments': [
            {'departure': point, 'arrival': point | {'airportCode': 'NRT'}, 'marketingCarrier': {'airlineCode': 'ZE', 'flightNumber': '0605'}},
        ]}],
        'fareMappings': [{'itineraryIds': itinerary_id, 'fares': [
            {'partnerCode': 'P1', 'fareType': 'A01', 'adult': {'totalFare': 100000}, 'reservationUrl': 'https://p1'},
            {'partnerCode': 'P2', 'fareType': 'A01', 'adult': {'totalFare': 90000}, 'reservationUrl': 'https://p2'},
        ]}],
    })
    options = NaverBookingOptionsResponseModel.model_validate({'data': {'bookingOptions': {'otaList': [
        {'otaId': 'P1', 'expName': '판매처1', 'fareType': 'A01', 'fare': 100000, 'active': {'realtime': True}, 'bookingUrl': 'https://t/1'},
        {'otaId': 'P2', 'expName': '판매처2', 'fareType': 'A01', 'fare': 90000, 'active': {'realtime': False}, 'bookingUrl': 'https://t/2'},
    ]}}})
    monkeypatch.setattr(NaverClient, 'run_request_flight_international_search_flights', lambda self, payload: event)
    monkeypatch.setattr(NaverClient, 'run_request_booking_options', lambda self, payload: options)
    legs = [NaverLeg(origin=NaverLocation(code='ICN', type='airport'), destination=NaverLocation(code='NRT', type='airport'), date='2026-11-15')]
    with NaverClient() as client:
        detail, notes = get_flight(client, flight_id(legs, NaverFlightConditions(), itinerary_id))
    assert [offer.seller for offer in detail.offers] == ['판매처1']
    assert (detail.price_per_adult_krw, detail.cheapest_seller, detail.seller_count) == (100000, '판매처1', 1)
    assert any('3/20곳' in note and '시간 상한' in note for note in notes)


def test_price_sort_keeps_cheap_fare_without_card_discount(monkeypatch: pytest.MonkeyPatch) -> None:
    # 서버 가격순은 카드 할인 포함 최저가라 카드 할인 없는 싼 항공권(ZE0607)이 뒤로 밀린다 (2026-10-21 ICN↔TYO: 대표 가격 2~4위가 11·13·14번째).
    # 더 받아 대표 가격 순으로 세운 뒤 잘라야 남는다. 서버가 주는 순서는 실제 API 로 골라 재현할 수 없어 가짜 응답으로 본다
    point = {'airportCode': 'ICN', 'dateTime': '202611150800'}
    prices = {'ZE0605': (300000, 200000), 'ZE0609': (280000, 210000), 'ZE0607': (250000, None)}
    event = NaverFlightInternationalSearchFlightsResponseModel.model_validate({
        'itineraries': [
            {'itineraryId': f'20261115ICNNRT{number}', 'segments': [
                {'departure': point, 'arrival': point | {'airportCode': 'NRT'}, 'marketingCarrier': {'airlineCode': 'ZE', 'flightNumber': number[2:]}},
            ]}
            for number in prices
        ],
        'fareMappings': [
            {'itineraryIds': f'20261115ICNNRT{number}', 'fares': [
                {'partnerCode': 'P1', 'fareType': 'A01', 'adult': {'totalFare': general}},
                *([{'partnerCode': 'P1', 'fareType': 'A01/B80', 'adult': {'totalFare': card}}] if card else []),
            ]}
            for number, (general, card) in prices.items()
        ],
    })
    limits: list[int] = []

    def search(
        self: NaverClient,
        payload: NaverFlightInternationalSearchFlightsRequestPayloadModel,
    ) -> NaverFlightInternationalSearchFlightsResponseModel:
        limits.append(payload.flight_filter.limit)
        return event

    monkeypatch.setattr(NaverClient, 'run_request_flight_international_search_flights', search)
    monkeypatch.setattr('baenggipyo.providers.naver.service.flight.partner_names', lambda client: {})
    legs = [NaverLeg(origin=NaverLocation(code='ICN', type='airport'), destination=NaverLocation(code='NRT', type='airport'), date='2026-11-15')]
    with NaverClient() as client:
        result = search_flights(client, legs, limit=2)
    assert limits == [6]
    assert [itinerary.price_per_adult_krw for itinerary in result.itineraries] == [250000, 280000]


# --- 항공권 ID ---

def test_flight_id_round_trips_search_conditions() -> None:
    legs = [
        NaverLeg(origin=NaverLocation(code='SEL', type='city'), destination=NaverLocation(code='NRT', type='airport'), date='2026-11-15'),
        NaverLeg(origin=NaverLocation(code='NRT', type='airport'), destination=NaverLocation(code='SEL', type='city'), date='2026-11-20'),
    ]
    conditions = NaverFlightConditions(adults=2, children=1, infants=0, cabin='비즈니스석', direct=True)
    itinerary_ids = '20261115ICNNRTKE0703+20261115NRTHNDKE0001-20261120NRTICNKE0704'
    parsed_legs, parsed_conditions, parsed_ids = parse_flight_id(flight_id(legs, conditions, itinerary_ids))
    assert [(leg.origin.code, leg.origin.type, leg.destination.code, leg.destination.type, leg.date) for leg in parsed_legs] == [
        ('SEL', 'city', 'NRT', 'airport', '2026-11-15'),
        ('NRT', 'airport', 'SEL', 'city', '2026-11-20'),
    ]
    assert parsed_conditions.model_dump() == conditions.model_dump(exclude={'airlines'}) | {'airlines': []}
    assert parsed_ids == itinerary_ids


# --- 국내선 ---

def test_domestic_fare_follows_site_formula_and_round_trips_id() -> None:
    # 1인 총액 = 운임 + 유류할증료 + 세금 + 발권 수수료 (사이트 번들의 계산식). 유아 운임은 사이트도 넣지 않아 합계를 모른다
    fare = NaverDomesticFareModel.model_validate({
        'agtCode': 'P1', 'adultFare': 15000, 'childFare': 15000, 'aFuel': 22000, 'cFuel': 22000,
        'aTax': 4000, 'cTax': 2000, 'publishFee': 1000, 'bookingClass': 'WEH3',
    })
    assert fare_per_person(fare) == (42000, 40000)
    assert fare_total(fare, NaverFlightConditions(adults=2, children=1)) == 124000
    assert fare_total(fare, NaverFlightConditions(adults=1, infants=1)) is None

    legs = [
        NaverLeg(origin=NaverLocation(code='GMP', type='airport'), destination=NaverLocation(code='CJU', type='airport'), date='2026-11-15'),
        NaverLeg(origin=NaverLocation(code='CJU', type='airport'), destination=NaverLocation(code='GMP', type='airport'), date='2026-11-17'),
    ]
    flight = NaverDomesticFlightModel.model_validate({
        'itineraryId': '202611172120CJUGMP7C142', 'type': 'arrival', 'seatClass': 'NB',
        'segment': {'airlineCode': '7C', 'flightNumber': '142', 'departure': {'airportCode': 'CJU'}, 'arrival': {'airportCode': 'GMP'}},
    })
    conditions = NaverFlightConditions(adults=2, cabin='비즈니스석')
    parsed_legs, parsed_conditions, direction, itinerary_id, seat_class = parse_domestic_flight_id(
        domestic_flight_id(legs, conditions, flight, 'arrival')
    )
    assert [(leg.origin.code, leg.destination.code, leg.date) for leg in parsed_legs] == [('GMP', 'CJU', '2026-11-15'), ('CJU', 'GMP', '2026-11-17')]
    assert (parsed_conditions.adults, parsed_conditions.cabin, direction, itinerary_id, seat_class) == (2, '비즈니스석', 'arrival', '202611172120CJUGMP7C142', 'NB')


# --- 검색 스트림 ---

def test_stream_keeps_received_event_when_next_line_times_out() -> None:
    # 늦은 판매처 때문에 다음 줄이 읽기 시간 상한 안에 오지 않는 상태는 실제 API 로 재현할 수 없어 가짜 줄로 본다
    def stalled_after_one_event():
        yield 'data:{"n": 1}'
        raise httpx.ReadTimeout('stalled')

    def stalled_before_any_event():
        raise httpx.ReadTimeout('stalled')
        yield

    assert last_event(stalled_after_one_event()) == '{"n": 1}'
    with pytest.raises(httpx.ReadTimeout):
        last_event(stalled_before_any_event())
