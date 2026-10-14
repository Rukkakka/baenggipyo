import time
import uuid
from collections.abc import (
    Awaitable,
    Callable,
)
from functools import cached_property
from urllib.parse import quote

from httpx import (
    AsyncClient,
    Client,
    HTTPStatusError,
    Response,
)
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
    should_retry_idempotent,
    wait_retry_after,
)
from baenggipyo.core.ratelimit import RateLimitedError
from baenggipyo.providers.skyscanner.api.request.airport import (
    SkyscannerAutosuggestSearchSearchFlightV1RequestParameterModel,
)
from baenggipyo.providers.skyscanner.api.request.calendar import (
    SkyscannerSearchIntentPricecalendarV1RequestPayloadModel,
)
from baenggipyo.providers.skyscanner.api.request.common import (
    CURRENCY,
    LOCALE,
    MARKET,
    SkyscannerRadarWebUnifiedSearchPollV2RequestParameterModel,
    SkyscannerRadarWebUnifiedSearchV2RequestPayloadModel,
)
from baenggipyo.providers.skyscanner.api.response.airport import (
    SkyscannerAutosuggestSearchSearchFlightV1ResponseModel,
)
from baenggipyo.providers.skyscanner.api.response.calendar import (
    SkyscannerSearchIntentPricecalendarV1ResponseModel,
)
from baenggipyo.providers.skyscanner.api.response.common import (
    SkyscannerRadarWebUnifiedSearchPollV2ResponseModel,
    SkyscannerRadarWebUnifiedSearchV2ResponseModel,
)

from typing import ClassVar


def should_retry_skyscanner(exc: BaseException) -> bool:
    """`should_retry_idempotent` 에서 500 을 뺀다.

    500 은 요청 값 때문에 늘 같은 응답이 오는 경우가 있다 (달력에 도시를 IATA `SEL` 로 주면 500 `Application error processing RPC`).
    시간당 요청 상한(30회)이 빠듯해 같은 실패에 요청을 세 번 쓰지 않는다. 502·503·504 같은 중계 오류와 timeout·transport 는 다시 보낸다.
    """
    if isinstance(exc, HTTPStatusError) and exc.response.status_code == 500:
        return False
    return should_retry_idempotent(exc)


skyscanner_retry = retry(
    retry=retry_if_exception(should_retry_skyscanner),
    stop=stop_after_attempt(3),
    wait=wait_retry_after,
    reraise=True,
    before_sleep=log_retry_before_sleep,
)
"""조회 전용 재시도: 실패하면 두 번까지 더 보낸다 (모두 3번), 500 을 뺀 5xx·429·timeout·transport. 요청 제한이 켜져 있으면 429 는 쿨다운으로 바로 끝난다."""


class SkyscannerClient(BaseHTTPClient):
    """스카이스캐너 (skyscanner.co.kr) 웹 비공식 API 클라이언트.

    공식 파트너 API 는 API 키가 필요해 쓰지 않고, 웹 페이지가 로그인 없이 부르는 `/g/` 아래 엔드포인트를 쓴다:
    자동완성, `radar` 검색(POST 후 `sessionId` 로 GET 폴링), `search-intent` 달력.

    action id 는 `/g/` 를 버린 경로 세그먼트를 snake_case 로 잇고 버전을 끝으로 옮긴다
    (`/g/radar/api/v2/web-unified-search/` → `radar_web_unified_search_v2`, `api` 세그먼트는 버린다). 같은 경로의 폴링 GET 은
    `radar_web_unified_search_poll_v2` 로 구분한다.

    검색 API 는 시장·로캘·통화를 `X-Skyscanner-Market`·`-Locale`·`-Currency` 헤더로 받는다 (KR·ko-KR·KRW).
    사이트에 PerimeterX 봇 차단이 있다. 차단은 403 + `{"reason": "blocked", "redirect_to": "/sttc/px/captcha-v2/…"}` 로 온다
    (2026-10-11 실측). 우회하지 않고, 요청 제한 쿨다운을 걸어 그동안 요청을 보내지 않는다 (`RateLimitedError`, reason=blocked).

    Args:
        host: 호스트 (스킴 제외). Defaults to 'www.skyscanner.co.kr'.
        rate_limit: 요청 제한 정책. Defaults to 3초 간격·분당 12회·시간당 30회·차단 후 30분 쿨다운(거듭되면 두 배씩, 최대 4시간).
            분당 상한으로는 수십 분에 걸친 누적을 막지 못해 시간당 상한을 둔다. 막히지 않는다는 보장은 아니다.

    Attributes:
        base_url: `url_schema` 와 `host` 로 만든 베이스 URL.
        view_id: 검색 헤더 `X-Skyscanner-ViewId` 에 넣는 클라이언트별 UUID.

    Example:
        >>> with SkyscannerClient() as client:
        ...     client.run_request_autosuggest_search_search_flight_v1(
        ...         SkyscannerAutosuggestSearchSearchFlightV1RequestParameterModel(query='도쿄')
        ...     )
    """

    autosuggest_search_search_flight_v1_endpoint: ClassVar[str] = '/g/autosuggest-search/api/v1/search-flight/{market}/{locale}/{query}'
    radar_web_unified_search_v2_endpoint: ClassVar[str] = '/g/radar/api/v2/web-unified-search/'
    radar_web_unified_search_poll_v2_endpoint: ClassVar[str] = '/g/radar/api/v2/web-unified-search/{session_id}'
    search_intent_pricecalendar_v1_endpoint: ClassVar[str] = '/g/search-intent/v1/pricecalendar'

    source_name: ClassVar[str] = 'skyscanner'
    default_rate_limit: ClassVar[dict[str, object]] = {
        'min_interval': 3.0,
        'max_per_minute': 12,
        'max_per_hour': 30,
        'cooldown': 1800,
        'max_cooldown': 14400,
    }

    host: str = 'www.skyscanner.co.kr'

    @property
    def base_url(self) -> str:
        return create_base_url(
            schema=self.url_schema,
            host=self.host
        )

    @property
    def base_header(self) -> dict[str, str]:
        return {
            'Accept': 'application/json',
            'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
            'Origin': 'https://www.skyscanner.co.kr',
            'Referer': 'https://www.skyscanner.co.kr/',
            'User-Agent': self.user_agent,
        }

    def _may_be_blocked(self, response: Response) -> bool:
        """본문을 읽어 차단 페이지인지 볼 응답인지 (403 이고 요청 제한이 켜져 있을 때만)."""
        return response.status_code == 403 and self.rate_limiter.policy.enabled

    def _raise_if_block_page(self, response: Response) -> None:
        """읽은 403 본문이 PerimeterX 차단 페이지면 쿨다운을 걸고 `RateLimitedError` 를 낸다. 본문 읽기만 sync·async 가 다르다."""
        if '"blocked"' not in response.text and 'captcha' not in response.text:
            return
        blocked_until = self.rate_limiter.block()
        raise RateLimitedError(self.source_name, blocked_until - time.time(), 'blocked')

    def _check_blocked(self, response: Response) -> None:
        """PerimeterX 차단 응답이면 쿨다운을 걸고 `RateLimitedError` 를 낸다. 403 본문을 보고 판정한다."""
        if self._may_be_blocked(response):
            response.read()
            self._raise_if_block_page(response)

    async def _acheck_blocked(self, response: Response) -> None:
        """Async variant of `_check_blocked`."""
        if self._may_be_blocked(response):
            await response.aread()
            self._raise_if_block_page(response)

    def rate_limit_hooks(self) -> dict[str, list[Callable[..., None]]]:
        hooks = super().rate_limit_hooks()
        hooks['response'].append(self._check_blocked)
        return hooks

    def async_rate_limit_hooks(self) -> dict[str, list[Callable[..., Awaitable[None]]]]:
        hooks = super().async_rate_limit_hooks()
        hooks['response'].append(self._acheck_blocked)
        return hooks

    @cached_property
    def view_id(self) -> str:
        return str(uuid.uuid4())

    @property
    def search_header(self) -> dict[str, str]:
        # 사이트 검색 요청이 붙이는 헤더 (FlightsDayView 번들). 시장·통화를 정하지 않으면 다른 통화로 올 수 있다
        return {
            'X-Skyscanner-ChannelId': 'website',
            'X-Skyscanner-Market': MARKET,
            'X-Skyscanner-Locale': LOCALE,
            'X-Skyscanner-Currency': CURRENCY,
            'X-Skyscanner-ViewId': self.view_id,
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

    def _autosuggest_url(self, parameter: SkyscannerAutosuggestSearchSearchFlightV1RequestParameterModel) -> str:
        return self.autosuggest_search_search_flight_v1_endpoint.format(
            market=parameter.market,
            locale=parameter.locale,
            query=quote(parameter.query, safe=''),
        )

    @skyscanner_retry
    def run_request_autosuggest_search_search_flight_v1(
        self,
        parameter: SkyscannerAutosuggestSearchSearchFlightV1RequestParameterModel
    ) -> SkyscannerAutosuggestSearchSearchFlightV1ResponseModel:
        """Suggest airports and cities via `GET /g/autosuggest-search/api/v1/search-flight/{market}/{locale}/{query}`.

        Retries up to 2 times (3 attempts in all) on 5xx other than 500, 429, timeouts and transport errors (`should_retry_skyscanner`).
        요청 제한이 켜져 있으면 429 는 재시도 없이 `RateLimitedError` 로 끝난다.

        Args:
            parameter: 검색어와 도착지 여부 (시장·로캘은 KR·ko-KR).

        Returns:
            SkyscannerAutosuggestSearchSearchFlightV1ResponseModel: 도시·공항·나라 후보 (엔티티 ID `GeoId` 포함).
        """
        response = self._client.get(
            url=self._autosuggest_url(parameter),
            params=parameter.model_dump(by_alias=True, exclude_none=True),
            headers={'Platform': 'web'}
        )
        raise_for_status(response=response)
        return SkyscannerAutosuggestSearchSearchFlightV1ResponseModel.model_validate_json(response.content)

    @skyscanner_retry
    async def arun_request_autosuggest_search_search_flight_v1(
        self,
        parameter: SkyscannerAutosuggestSearchSearchFlightV1RequestParameterModel
    ) -> SkyscannerAutosuggestSearchSearchFlightV1ResponseModel:
        """Async variant of `run_request_autosuggest_search_search_flight_v1`."""
        response = await self._async_client.get(
            url=self._autosuggest_url(parameter),
            params=parameter.model_dump(by_alias=True, exclude_none=True),
            headers={'Platform': 'web'}
        )
        raise_for_status(response=response)
        return SkyscannerAutosuggestSearchSearchFlightV1ResponseModel.model_validate_json(response.content)

    @skyscanner_retry
    def run_request_radar_web_unified_search_v2(
        self,
        payload: SkyscannerRadarWebUnifiedSearchV2RequestPayloadModel
    ) -> SkyscannerRadarWebUnifiedSearchV2ResponseModel:
        """Start a flight or everywhere search via `POST /g/radar/api/v2/web-unified-search/`.

        Same retry behaviour as `run_request_autosuggest_search_search_flight_v1` (조회 전용이라 POST 라도 재시도한다).
        `context.status` 가 `incomplete` 면 `run_request_radar_web_unified_search_poll_v2` 로 이어 받는다. 어디든지 검색은 바로
        `everywhereDestination` 이 complete 로 온다.

        Args:
            payload: 구간(엔티티 ID·날짜)·승객·좌석, 어디든지 결과 단위.

        Returns:
            SkyscannerRadarWebUnifiedSearchV2ResponseModel: 상태·세션 ID 와 그때까지의 결과.
        """
        response = self._client.post(
            url=self.radar_web_unified_search_v2_endpoint,
            json=payload.model_dump(by_alias=True, exclude_none=True),
            headers=self.search_header
        )
        raise_for_status(response=response)
        return SkyscannerRadarWebUnifiedSearchV2ResponseModel.model_validate_json(response.content)

    @skyscanner_retry
    async def arun_request_radar_web_unified_search_v2(
        self,
        payload: SkyscannerRadarWebUnifiedSearchV2RequestPayloadModel
    ) -> SkyscannerRadarWebUnifiedSearchV2ResponseModel:
        """Async variant of `run_request_radar_web_unified_search_v2`."""
        response = await self._async_client.post(
            url=self.radar_web_unified_search_v2_endpoint,
            json=payload.model_dump(by_alias=True, exclude_none=True),
            headers=self.search_header
        )
        raise_for_status(response=response)
        return SkyscannerRadarWebUnifiedSearchV2ResponseModel.model_validate_json(response.content)

    @skyscanner_retry
    def run_request_radar_web_unified_search_poll_v2(
        self,
        parameter: SkyscannerRadarWebUnifiedSearchPollV2RequestParameterModel
    ) -> SkyscannerRadarWebUnifiedSearchPollV2ResponseModel:
        """Poll a running search via `GET /g/radar/api/v2/web-unified-search/{sessionId}`.

        Same retry behaviour as `run_request_autosuggest_search_search_flight_v1`. 한 번 부를 뿐 반복하지 않는다 (간격·상한은
        service). 1시간 지난 세션도 결과를 줬다 (2026-10-11 실측).

        Args:
            parameter: 세션 ID.

        Returns:
            SkyscannerRadarWebUnifiedSearchPollV2ResponseModel: 상태와 그때까지의 결과 전체.
        """
        response = self._client.get(
            url=self.radar_web_unified_search_poll_v2_endpoint.format(session_id=quote(parameter.session_id, safe='')),
            headers=self.search_header
        )
        raise_for_status(response=response)
        return SkyscannerRadarWebUnifiedSearchPollV2ResponseModel.model_validate_json(response.content)

    @skyscanner_retry
    async def arun_request_radar_web_unified_search_poll_v2(
        self,
        parameter: SkyscannerRadarWebUnifiedSearchPollV2RequestParameterModel
    ) -> SkyscannerRadarWebUnifiedSearchPollV2ResponseModel:
        """Async variant of `run_request_radar_web_unified_search_poll_v2`."""
        response = await self._async_client.get(
            url=self.radar_web_unified_search_poll_v2_endpoint.format(session_id=quote(parameter.session_id, safe='')),
            headers=self.search_header
        )
        raise_for_status(response=response)
        return SkyscannerRadarWebUnifiedSearchPollV2ResponseModel.model_validate_json(response.content)

    @skyscanner_retry
    def run_request_search_intent_pricecalendar_v1(
        self,
        payload: SkyscannerSearchIntentPricecalendarV1RequestPayloadModel
    ) -> SkyscannerSearchIntentPricecalendarV1ResponseModel:
        """Get one-way estimated lowest prices per day via `POST /g/search-intent/v1/pricecalendar`.

        Same retry behaviour as `run_request_autosuggest_search_search_flight_v1`. 도시를 IATA(`SEL`)로 주면 500 이다
        (스카이 ID `SELA` 를 준다).

        Args:
            payload: 출발·도착 스카이 ID.

        Returns:
            SkyscannerSearchIntentPricecalendarV1ResponseModel: 오늘부터 366일의 추정 최저가.
        """
        response = self._client.post(
            url=self.search_intent_pricecalendar_v1_endpoint,
            json=payload.model_dump(by_alias=True, exclude_none=True)
        )
        raise_for_status(response=response)
        return SkyscannerSearchIntentPricecalendarV1ResponseModel.model_validate_json(response.content)

    @skyscanner_retry
    async def arun_request_search_intent_pricecalendar_v1(
        self,
        payload: SkyscannerSearchIntentPricecalendarV1RequestPayloadModel
    ) -> SkyscannerSearchIntentPricecalendarV1ResponseModel:
        """Async variant of `run_request_search_intent_pricecalendar_v1`."""
        response = await self._async_client.post(
            url=self.search_intent_pricecalendar_v1_endpoint,
            json=payload.model_dump(by_alias=True, exclude_none=True)
        )
        raise_for_status(response=response)
        return SkyscannerSearchIntentPricecalendarV1ResponseModel.model_validate_json(response.content)
