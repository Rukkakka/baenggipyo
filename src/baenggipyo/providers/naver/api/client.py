import json
import time
from functools import cached_property

from httpx import (
    AsyncClient,
    Client,
    HTTPStatusError,
    TimeoutException,
    TransportError,
)
from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
)

from baenggipyo.core.http import (
    BaseHTTPClient,
    create_base_url,
    log_retry_before_sleep,
    raise_for_status,
    wait_retry_after,
)
from baenggipyo.providers.naver.api.request.airport import (
    AUTOCOMPLETE_LOCATIONS_QUERY,
    NaverAutocompleteLocationsRequestPayloadModel,
)
from baenggipyo.providers.naver.api.request.calendar import (
    MIN_PRICES_BY_DATE_QUERY,
    NaverMinPricesByDateRequestPayloadModel,
)
from baenggipyo.providers.naver.api.request.common import (
    BOOKING_PARTNERS_QUERY,
    NaverBookingPartnersRequestPayloadModel,
)
from baenggipyo.providers.naver.api.request.domestic import (
    BOOKING_OPTIONS_DOMESTIC_QUERY,
    NaverBookingOptionsDomesticRequestPayloadModel,
    NaverFlightDomesticSearchFlightsRequestPayloadModel,
)
from baenggipyo.providers.naver.api.request.explore import (
    MIN_PRICES_BY_DESTINATION_QUERY,
    NaverMinPricesByDestinationRequestPayloadModel,
)
from baenggipyo.providers.naver.api.request.flight import (
    BOOKING_OPTIONS_QUERY,
    NaverBookingOptionsRequestPayloadModel,
    NaverFlightInternationalSearchFlightsRequestPayloadModel,
)
from baenggipyo.providers.naver.api.response.airport import (
    NaverAutocompleteLocationsResponseModel,
)
from baenggipyo.providers.naver.api.response.calendar import (
    NaverMinPricesByDateResponseModel,
)
from baenggipyo.providers.naver.api.response.common import (
    NaverBookingPartnersResponseModel,
)
from baenggipyo.providers.naver.api.response.domestic import (
    NaverBookingOptionsDomesticResponseModel,
    NaverFlightDomesticSearchFlightsResponseModel,
)
from baenggipyo.providers.naver.api.response.explore import (
    NaverMinPricesByDestinationResponseModel,
)
from baenggipyo.providers.naver.api.response.flight import (
    NaverBookingOptionsResponseModel,
    NaverFlightInternationalSearchFlightsResponseModel,
)
from baenggipyo.providers.naver.api.stream import (
    alast_event,
    last_event,
)

from typing import (
    ClassVar,
    TypeVar,
)


_R = TypeVar('_R', bound=BaseModel)

# 판매처 20곳을 서버가 부르는 동안 SSE 를 받는다 (편도 9초, 왕복 17초 실측). 이보다 길면 받은 데까지 쓴다
SEARCH_STREAM_SECONDS: float = 60.0


def should_retry_naver(exc: BaseException) -> bool:
    """5xx 와 timeout/transport 오류만 재시도한다.

    429 는 봇 차단이라 짧게 다시 보내면 차단을 늘릴 뿐이다 (요청 제한이 켜져 있으면 쿨다운으로 끝난다).
    """
    if isinstance(exc, HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, (TimeoutException, TransportError))


naver_retry = retry(
    retry=retry_if_exception(should_retry_naver),
    stop=stop_after_attempt(2),
    wait=wait_retry_after,
    reraise=True,
    before_sleep=log_retry_before_sleep,
)
"""조회 전용 공통 재시도: 실패하면 1초 뒤 한 번만 더 보낸다 (모두 2번), 5xx·timeout·transport 오류만. 검색 한 번이 판매처 20곳을 부르므로 한 번만 더 한다."""


def _search_event(event: str | None) -> dict[str, object]:
    """SSE 마지막 이벤트 본문을 JSON 으로. 이벤트가 없거나 검색 결과 이벤트가 아니면 `ValueError` (`PARSE_ERROR`).

    검색 결과 이벤트는 새 검색·캐시 조회·만료된 캐시 조회 모두 `status` 나 `isExpired` 를 준다 (2026-10-17 실측: 국제선 새 검색·캐시 조회·
    없는 version 의 만료 응답, 국내선 검색). 둘 다 없는 이벤트(에러 본문 등)를 모든 필드가 선택인 응답 모델로 검증하면 결과 0건인 정상 검색이 된다.
    """
    if event is None:
        raise ValueError('네이버 항공권 검색 응답에 이벤트가 없습니다')
    loaded = json.loads(event)
    if not isinstance(loaded, dict) or not {'status', 'isExpired'} & loaded.keys():
        # 값에는 추적 ID 같은 것이 섞일 수 있어 키 이름만 싣는다
        shape = f"키: {', '.join(sorted(map(str, loaded))[:10])}" if isinstance(loaded, dict) else f'JSON {type(loaded).__name__}'
        raise ValueError(f'네이버 항공권 검색 응답의 마지막 이벤트가 검색 결과가 아닙니다 ({shape})')
    return loaded


class NaverClient(BaseHTTPClient):
    """네이버 항공권 (flight.naver.com) 비공식 API 클라이언트.

    API 는 `flight-api.naver.com` 하나다. 항공권 검색만 REST(SSE) `POST /flight/international/searchFlights`·
    `/flight/domestic/searchFlights` 이고, 공항
    자동완성·판매처·달력·어디든지는 `POST /graphql` 이다. GraphQL 인트로스펙션이 막혀 있어 쿼리 문자열은 사이트 번들의 것을
    필요한 필드만 남겨 쓴다 (`api/request/`). GraphQL 실패는 HTTP 200 의 `errors` 로 오며 여기서 판정하지 않는다.

    action id 는 GraphQL 루트 필드 이름을 snake_case 로 쓴다 (`autocompleteLocations` → `autocomplete_locations`,
    사이트 오퍼레이션 이름이 `getOtaList` 인 `bookingOptions` → `booking_options`). REST 는 경로 세그먼트를 잇는다.

    브라우저 `User-Agent` 와 `Accept-Language` 가 둘 다 없으면 모든 요청이 본문 없는 500 이다 (2026-10-11 실측).
    `base_header` 에서 늘 함께 보낸다.

    Args:
        host: 호스트 (스킴 제외). Defaults to 'flight-api.naver.com'.
        rate_limit: 요청 제한 정책. Defaults to 1초 간격·분당 30회·429 후 15분 쿨다운 (한계를 재지 않은 임시값).

    Attributes:
        base_url: `url_schema` 와 `host` 로 만든 베이스 URL.

    Example:
        >>> with NaverClient() as client:
        ...     client.run_request_autocomplete_locations(
        ...         NaverAutocompleteLocationsRequestPayloadModel(query='도쿄')
        ...     )
    """

    graphql_endpoint: ClassVar[str] = '/graphql'
    flight_international_search_flights_endpoint: ClassVar[str] = '/flight/international/searchFlights'
    flight_domestic_search_flights_endpoint: ClassVar[str] = '/flight/domestic/searchFlights'

    source_name: ClassVar[str] = 'naver'
    default_rate_limit: ClassVar[dict[str, object]] = {
        'min_interval': 1.0,
        'max_per_minute': 30,
        'cooldown': 900,
    }

    host: str = 'flight-api.naver.com'

    @property
    def base_url(self) -> str:
        return create_base_url(
            schema=self.url_schema,
            host=self.host
        )

    @property
    def base_header(self) -> dict[str, str]:
        return {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
            'Origin': 'https://flight.naver.com',
            'Referer': 'https://flight.naver.com/',
            'User-Agent': self.user_agent,
        }

    @cached_property
    def _client(self) -> Client:
        return Client(
            base_url=self.base_url,
            headers=self.base_header,
            event_hooks=self.rate_limit_hooks(),
            verify=self.verify,
            timeout=self.timeout,
            proxy=self.proxy
        )

    @cached_property
    def _async_client(self) -> AsyncClient:
        return AsyncClient(
            base_url=self.base_url,
            headers=self.base_header,
            event_hooks=self.async_rate_limit_hooks(),
            verify=self.verify,
            timeout=self.timeout,
            proxy=self.proxy
        )

    @staticmethod
    def _graphql_body(operation: str, query: str, payload: BaseModel) -> dict[str, object]:
        return {
            'operationName': operation,
            'query': query,
            'variables': payload.model_dump(by_alias=True, exclude_none=True),
        }

    def _graphql(self, operation: str, query: str, payload: BaseModel, model: type[_R]) -> _R:
        response = self._client.post(
            url=self.graphql_endpoint,
            json=self._graphql_body(operation, query, payload)
        )
        raise_for_status(response=response)
        return model.model_validate(response.json())

    async def _agraphql(self, operation: str, query: str, payload: BaseModel, model: type[_R]) -> _R:
        response = await self._async_client.post(
            url=self.graphql_endpoint,
            json=self._graphql_body(operation, query, payload)
        )
        raise_for_status(response=response)
        return model.model_validate(response.json())

    def _stream_last_event(self, endpoint: str, payload: BaseModel, stream_seconds: float) -> dict[str, object]:
        """SSE 를 끝까지(또는 스트림이 열린 뒤 `stream_seconds` 까지) 읽어 마지막 이벤트를 JSON 으로 돌려준다.

        이벤트가 없거나 검색 결과가 아닌 응답은 `BaenggipyoError` 가 아니라 `ValueError` 로 던진다. client 는 응답 검증만 하고 에러 코드를 정하지
        않으며(service 가 정한다), 형식이 어긋난 응답은 `handle_errors` 가 `ValueError` 를 `PARSE_ERROR`(차단 페이지·구조 변경 힌트)로
        낸다 (output-contract.md '에러'). JSON 이 아닌 응답의 `JSONDecodeError` 와 같은 경로다.

        Raises:
            ValueError: 이벤트가 하나도 없거나 마지막 이벤트가 검색 결과가 아닐 때 (`PARSE_ERROR`, `_search_event`).
        """
        with self._client.stream(
            'POST',
            url=endpoint,
            json=payload.model_dump(by_alias=True, exclude_none=True),
            headers={'Accept': 'text/event-stream'}
        ) as response:
            if response.is_error:
                response.read()
                raise_for_status(response=response)
            # 요청 제한 훅의 슬롯 대기(최대 max_wait)가 스트림 시간을 깎지 않게 스트림이 열린 뒤부터 잰다
            deadline = time.monotonic() + stream_seconds
            event = last_event(response.iter_lines(), deadline)
        return _search_event(event)

    async def _astream_last_event(self, endpoint: str, payload: BaseModel, stream_seconds: float) -> dict[str, object]:
        """Async variant of `_stream_last_event`."""
        async with self._async_client.stream(
            'POST',
            url=endpoint,
            json=payload.model_dump(by_alias=True, exclude_none=True),
            headers={'Accept': 'text/event-stream'}
        ) as response:
            if response.is_error:
                await response.aread()
                raise_for_status(response=response)
            deadline = time.monotonic() + stream_seconds
            event = await alast_event(response.aiter_lines(), deadline)
        return _search_event(event)

    @naver_retry
    def run_request_flight_international_search_flights(
        self,
        payload: NaverFlightInternationalSearchFlightsRequestPayloadModel,
        stream_seconds: float = SEARCH_STREAM_SECONDS,
    ) -> NaverFlightInternationalSearchFlightsResponseModel:
        """Search international flights via `POST /flight/international/searchFlights` (SSE).

        Retries once (2 attempts in all) on 5xx, timeouts and transport errors. 요청 제한이 켜져 있으면 429 는 재시도 없이
        `RateLimitedError` 로 끝난다.

        응답은 판매처 하나가 끝날 때마다 결과 전체를 다시 보내는 SSE 다. 스트림을 끝까지 읽고 마지막 이벤트만 검증한다
        (중간에 끊으면 서버가 검색을 버린다). `stream_seconds` 가 지나거나 다음 이벤트가 읽기 시간 상한(`timeout`) 안에 오지
        않으면 그때까지 받은 마지막 이벤트를 돌려주며, 그 경우 `status.is_completed` 가 False 다. 캐시 조회(`initial_request=False`)는
        이벤트 하나가 바로 온다.

        Args:
            payload: 여정·승객·좌석·직항·서버 필터(`flight_filter`)와 새 검색/캐시 조회 구분.
            stream_seconds: 스트림이 열린 뒤 받을 최대 시간 (초). 요청 제한 대기는 넣지 않는다.

        Returns:
            NaverFlightInternationalSearchFlightsResponseModel: 마지막 이벤트 (여정·운임·진행 상태·코드표).

        Raises:
            ValueError: 이벤트가 하나도 없거나 마지막 이벤트가 검색 결과가 아닐 때 (`PARSE_ERROR`).
        """
        event = self._stream_last_event(self.flight_international_search_flights_endpoint, payload, stream_seconds)
        return NaverFlightInternationalSearchFlightsResponseModel.model_validate(event)

    @naver_retry
    async def arun_request_flight_international_search_flights(
        self,
        payload: NaverFlightInternationalSearchFlightsRequestPayloadModel,
        stream_seconds: float = SEARCH_STREAM_SECONDS,
    ) -> NaverFlightInternationalSearchFlightsResponseModel:
        """Async variant of `run_request_flight_international_search_flights`."""
        event = await self._astream_last_event(self.flight_international_search_flights_endpoint, payload, stream_seconds)
        return NaverFlightInternationalSearchFlightsResponseModel.model_validate(event)

    @naver_retry
    def run_request_flight_domestic_search_flights(
        self,
        payload: NaverFlightDomesticSearchFlightsRequestPayloadModel,
        stream_seconds: float = SEARCH_STREAM_SECONDS,
    ) -> NaverFlightDomesticSearchFlightsResponseModel:
        """Search domestic flights via `POST /flight/domestic/searchFlights` (SSE).

        Same retry behaviour as `run_request_flight_international_search_flights`. 국제선과 같은 SSE 이고 완료 표시는
        `status.isComplete` 다. 편 목록은 `flight_filter.filter.type` 의 방향(가는 편·오는 편)만 온다. 결과가 작다 (편도 30KB,
        2026-10-11 실측).

        Args:
            payload: 구간(공항 코드)·승객·편도/왕복·서버 필터와 새 검색/캐시 조회 구분.
            stream_seconds: 스트림이 열린 뒤 받을 최대 시간 (초). 요청 제한 대기는 넣지 않는다.

        Returns:
            NaverFlightDomesticSearchFlightsResponseModel: 마지막 이벤트 (편·판매처별 운임·진행 상태).

        Raises:
            ValueError: 이벤트가 하나도 없거나 마지막 이벤트가 검색 결과가 아닐 때 (`PARSE_ERROR`).
        """
        event = self._stream_last_event(self.flight_domestic_search_flights_endpoint, payload, stream_seconds)
        return NaverFlightDomesticSearchFlightsResponseModel.model_validate(event)

    @naver_retry
    async def arun_request_flight_domestic_search_flights(
        self,
        payload: NaverFlightDomesticSearchFlightsRequestPayloadModel,
        stream_seconds: float = SEARCH_STREAM_SECONDS,
    ) -> NaverFlightDomesticSearchFlightsResponseModel:
        """Async variant of `run_request_flight_domestic_search_flights`."""
        event = await self._astream_last_event(self.flight_domestic_search_flights_endpoint, payload, stream_seconds)
        return NaverFlightDomesticSearchFlightsResponseModel.model_validate(event)

    @naver_retry
    def run_request_booking_options_domestic(
        self,
        payload: NaverBookingOptionsDomesticRequestPayloadModel
    ) -> NaverBookingOptionsDomesticResponseModel:
        """List booking partners of domestic flights via `POST /graphql` (`bookingOptions`, searchType DOMESTIC).

        Same retry behaviour as `run_request_flight_international_search_flights`. 국제선 `booking_options` 와 루트 필드가
        같아 action id 를 `booking_options_domestic` 으로 구분한다.

        Args:
            payload: 검색 조건, 판매처마다 고른 편 전부의 운임(`bookingParams`), 편 ID.

        Returns:
            NaverBookingOptionsDomesticResponseModel: 한 판매처 운임(`otaList`)과 판매처가 다른 왕복 조합(`combined`) 또는 `errors`.
        """
        return self._graphql('bookingOptions', BOOKING_OPTIONS_DOMESTIC_QUERY, payload, NaverBookingOptionsDomesticResponseModel)

    @naver_retry
    async def arun_request_booking_options_domestic(
        self,
        payload: NaverBookingOptionsDomesticRequestPayloadModel
    ) -> NaverBookingOptionsDomesticResponseModel:
        """Async variant of `run_request_booking_options_domestic`."""
        return await self._agraphql('bookingOptions', BOOKING_OPTIONS_DOMESTIC_QUERY, payload, NaverBookingOptionsDomesticResponseModel)

    @naver_retry
    def run_request_autocomplete_locations(
        self,
        payload: NaverAutocompleteLocationsRequestPayloadModel
    ) -> NaverAutocompleteLocationsResponseModel:
        """Search airports and cities via `POST /graphql` (`autocompleteLocations`).

        Same retry behaviour as `run_request_flight_international_search_flights`.

        Args:
            payload: 검색어 (이름 또는 코드)와 공항만 찾을지.

        Returns:
            NaverAutocompleteLocationsResponseModel: 도시·공항·나라 후보 또는 `errors`.
        """
        return self._graphql('autocompleteLocations', AUTOCOMPLETE_LOCATIONS_QUERY, payload, NaverAutocompleteLocationsResponseModel)

    @naver_retry
    async def arun_request_autocomplete_locations(
        self,
        payload: NaverAutocompleteLocationsRequestPayloadModel
    ) -> NaverAutocompleteLocationsResponseModel:
        """Async variant of `run_request_autocomplete_locations`."""
        return await self._agraphql('autocompleteLocations', AUTOCOMPLETE_LOCATIONS_QUERY, payload, NaverAutocompleteLocationsResponseModel)

    @naver_retry
    def run_request_booking_options(
        self,
        payload: NaverBookingOptionsRequestPayloadModel
    ) -> NaverBookingOptionsResponseModel:
        """List booking partners of one itinerary via `POST /graphql` (`getOtaList` → `bookingOptions`).

        Same retry behaviour as `run_request_flight_international_search_flights`. 운임의 `bookingUrl` 이 비어 있으면
        빈 목록이 온다 (검색 캐시 조회로 채운 reservationUrl 을 넘긴다).

        Args:
            payload: 검색 조건, 항공권의 판매처별 운임, 여정 ID.

        Returns:
            NaverBookingOptionsResponseModel: 판매처 이름·순위·예약 링크 또는 `errors`.
        """
        return self._graphql('getOtaList', BOOKING_OPTIONS_QUERY, payload, NaverBookingOptionsResponseModel)

    @naver_retry
    async def arun_request_booking_options(
        self,
        payload: NaverBookingOptionsRequestPayloadModel
    ) -> NaverBookingOptionsResponseModel:
        """Async variant of `run_request_booking_options`."""
        return await self._agraphql('getOtaList', BOOKING_OPTIONS_QUERY, payload, NaverBookingOptionsResponseModel)

    @naver_retry
    def run_request_booking_partners(
        self,
        payload: NaverBookingPartnersRequestPayloadModel
    ) -> NaverBookingPartnersResponseModel:
        """List partner names via `POST /graphql` (`bookingPartners`).

        Same retry behaviour as `run_request_flight_international_search_flights`. 판매처 코드 중 항공사 직판 일부는
        목록에 없다 (검색 판매처 31곳 중 26곳, 2026-10-11 실측).

        Args:
            payload: 변수 없음.

        Returns:
            NaverBookingPartnersResponseModel: 판매처 코드 → 이름 또는 `errors`.
        """
        return self._graphql('bookingPartners', BOOKING_PARTNERS_QUERY, payload, NaverBookingPartnersResponseModel)

    @naver_retry
    async def arun_request_booking_partners(
        self,
        payload: NaverBookingPartnersRequestPayloadModel
    ) -> NaverBookingPartnersResponseModel:
        """Async variant of `run_request_booking_partners`."""
        return await self._agraphql('bookingPartners', BOOKING_PARTNERS_QUERY, payload, NaverBookingPartnersResponseModel)

    @naver_retry
    def run_request_min_prices_by_date(
        self,
        payload: NaverMinPricesByDateRequestPayloadModel
    ) -> NaverMinPricesByDateResponseModel:
        """Get lowest prices per date via `POST /graphql` (`minPricesByDate`).

        Same retry behaviour as `run_request_flight_international_search_flights`. 실시간 가격이 아니라 최근 검색에서
        모은 값이다.

        Args:
            payload: 출발·도착 코드와 종류, 편도·왕복, 왕복 묶음 방식·여행 일수, 직항.

        Returns:
            NaverMinPricesByDateResponseModel: 날짜별 최저가 또는 `errors`.
        """
        return self._graphql('minPricesByDate', MIN_PRICES_BY_DATE_QUERY, payload, NaverMinPricesByDateResponseModel)

    @naver_retry
    async def arun_request_min_prices_by_date(
        self,
        payload: NaverMinPricesByDateRequestPayloadModel
    ) -> NaverMinPricesByDateResponseModel:
        """Async variant of `run_request_min_prices_by_date`."""
        return await self._agraphql('minPricesByDate', MIN_PRICES_BY_DATE_QUERY, payload, NaverMinPricesByDateResponseModel)

    @naver_retry
    def run_request_min_prices_by_destination(
        self,
        payload: NaverMinPricesByDestinationRequestPayloadModel
    ) -> NaverMinPricesByDestinationResponseModel:
        """Get lowest prices per destination (everywhere) via `POST /graphql` (`minPricesByDestination`).

        Same retry behaviour as `run_request_flight_international_search_flights`. 결과는 인기순이고 가격은 최근 검색에서
        모은 값이다.

        Args:
            payload: 출발 코드, 편도·왕복, 출발 달, 여행 일수, 직항, 국내·국제, 목적지 수.

        Returns:
            NaverMinPricesByDestinationResponseModel: 목적지별 최저가 또는 `errors`.
        """
        return self._graphql('minPricesByDestination', MIN_PRICES_BY_DESTINATION_QUERY, payload, NaverMinPricesByDestinationResponseModel)

    @naver_retry
    async def arun_request_min_prices_by_destination(
        self,
        payload: NaverMinPricesByDestinationRequestPayloadModel
    ) -> NaverMinPricesByDestinationResponseModel:
        """Async variant of `run_request_min_prices_by_destination`."""
        return await self._agraphql('minPricesByDestination', MIN_PRICES_BY_DESTINATION_QUERY, payload, NaverMinPricesByDestinationResponseModel)
