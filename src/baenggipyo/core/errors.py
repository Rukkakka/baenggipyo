from typing import Any


class BaenggipyoError(Exception):
    """CLI 가 구조화된 에러로 보고하는 애플리케이션 에러.

    Args:
        code: 기계가 읽는 에러 코드 (예: AIRPORT_NOT_FOUND).
        message: 사람이 읽는 설명.
        hint: 다음에 시도할 행동. Defaults to None.
        details: 에러 JSON 에 함께 실을 추가 필드. Defaults to None.
    """

    def __init__(
        self,
        code: str,
        message: str,
        hint: str | None = None,
        details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details or {}


class AirportNotFoundError(BaenggipyoError):
    """공항·도시 이름이나 코드를 찾지 못했을 때.

    Args:
        query: 사용자가 준 공항·도시 이름 또는 코드.
        hint: 다음에 시도할 행동. Defaults to None.
    """

    def __init__(self, query: str, hint: str | None = None) -> None:
        super().__init__(
            code='AIRPORT_NOT_FOUND',
            message=f"'{query}' 에 해당하는 공항·도시를 찾지 못했습니다",
            hint=hint or "search airports 로 IATA 코드(예: 'ICN', 'NRT')를 찾아 다시 주세요",
        )


class AirportAmbiguousError(BaenggipyoError):
    """공항·도시 이름이 여러 곳에 맞아 어느 곳인지 정할 수 없을 때.

    첫 번째 후보로 검색하면 엉뚱한 노선이 나오기 쉬워 검색하지 않고 후보를 알려 코드로 다시 묻게 한다.

    Args:
        query: 사용자가 준 검색어.
        candidates: 후보 (앞의 몇 곳, 'NRT 나리타 (공항, 일본)' 처럼 코드가 맨 앞인 표기, `core.flight.candidate_label`).
        total: 전체 후보 수.
    """

    def __init__(self, query: str, candidates: list[str], total: int) -> None:
        # hint 예시는 다음 입력에 그대로 넣을 코드만 보인다 (후보 표기 전체를 넣으면 그 문자열을 입력으로 넘기기 쉽다)
        example = candidates[0].split(' ', 1)[0] if candidates else 'NRT'
        super().__init__(
            code='AIRPORT_AMBIGUOUS',
            message=f"'{query}' 에 해당하는 공항·도시가 {total}곳입니다",
            hint=f'후보 중 하나의 IATA 코드로 다시 검색하세요 (예: {example})',
            details={'candidates': candidates, 'candidate_count': total},
        )


class RouteNotSupportedError(BaenggipyoError):
    """소스가 그 노선을 다루지 않을 때 (네이버의 국내선 다구간, 국내·국제가 섞인 노선 등).

    Args:
        source: 소스 이름.
        route: 노선 표기 (예: 'GMP-CJU').
        reason: 다루지 않는 노선 종류 (예: '국내선 다구간').
        alternative: 이 노선을 찾을 수 있는 다른 소스 이름.
    """

    def __init__(self, source: str, route: str, reason: str, alternative: str) -> None:
        super().__init__(
            code='ROUTE_NOT_SUPPORTED',
            message=f'{source} 는 {reason} 노선을 찾지 않습니다: {route}',
            hint=f'baenggipyo {alternative} 로 같은 노선을 찾으세요',
            details={'route': route},
        )


class FareUnavailableError(BaenggipyoError):
    """항공권이 매진·만료됐거나 검색 결과가 만료돼 더 이상 살 수 없을 때.

    Args:
        source: 소스 이름.
        itinerary_id: 항공권 ID.
        status: 소스가 알려준 상태값. 모르면 None.
        reason: 판매 불가 사유 (예: SOLD_OUT, SEARCH_EXPIRED). 모르면 None.
        hint: 기본 hint(후보에서 빼고 다시 검색) 대신 쓸 문구. Defaults to None.
    """

    def __init__(
        self,
        source: str,
        itinerary_id: str,
        status: str | None = None,
        reason: str | None = None,
        hint: str | None = None
    ) -> None:
        detail = ', '.join(part for part in (status and f'status={status}', reason and f'reason={reason}') if part)
        super().__init__(
            code='FARE_UNAVAILABLE',
            message=f'현재 살 수 없는 {source} 항공권입니다: {itinerary_id}' + (f' ({detail})' if detail else ' (없거나 만료된 항공권)'),
            hint=hint or '매진됐거나 검색 결과가 만료된 항공권입니다. 이 항공권은 빼고 search flights 로 다시 찾으세요',
            details={'itinerary_id': itinerary_id, 'fare_status': status, 'reason': reason},
        )


class SearchIncompleteError(BaenggipyoError):
    """`flight` 가 다시 묻는 검색이 시간 상한 안에 끝나지 않아 그 항공권을 확인하지 못했을 때.

    매진이 아니라 늦게 끝난 것이라 `FareUnavailableError` 로 내지 않는다. 그 hint(후보에서 빼라)를 따르면 아직 파는 항공권을 버린다.

    Args:
        source: 소스 이름.
        itinerary_id: 항공권 ID (사용자가 넣은 값).
        alternative: 같은 노선을 찾을 수 있는 다른 소스 이름.
    """

    def __init__(self, source: str, itinerary_id: str, alternative: str) -> None:
        super().__init__(
            code='UPSTREAM_ERROR',
            message=f'{source} 검색이 시간 상한 안에 끝나지 않아 이 항공권을 아직 확인하지 못했습니다: {itinerary_id}',
            hint=f'매진이 아니라 검색이 늦게 끝난 것입니다. 잠시 뒤 같은 id 로 다시 부르거나 {alternative} 로 찾으세요',
            details={'itinerary_id': itinerary_id, 'reason': 'SEARCH_INCOMPLETE'},
        )


def fare_missing_error(
    source: str,
    itinerary_id: str,
    *,
    cut_short: bool,
    reason: str,
    alternative: str,
    unavailable_hint: str | None = None,
) -> BaenggipyoError:
    """`flight` 가 다시 받은 검색 결과에 그 항공권이 없을 때 낼 에러.

    끝까지 받은 검색에 없으면 `FareUnavailableError` 다. 검색이 끝나기 전에 멈췄으면 매진이 아니라 늦게 끝난 것일 수 있어
    `SearchIncompleteError` 로 낸다 (`FareUnavailableError` 의 hint 를 따르면 아직 파는 항공권을 버린다).

    Args:
        source: 소스 이름.
        itinerary_id: 항공권 ID (사용자가 넣은 값).
        cut_short: 검색이 끝나기 전에(시간 상한 등) 멈췄는지.
        reason: 끝까지 받은 검색에 없을 때의 판매 불가 사유 (NOT_IN_RESULTS, SEARCH_EXPIRED).
        alternative: 같은 노선을 찾을 수 있는 다른 소스 이름.
        unavailable_hint: `FareUnavailableError` 의 기본 hint 대신 쓸 문구. Defaults to None.
    """
    if cut_short:
        return SearchIncompleteError(source, itinerary_id, alternative)
    return FareUnavailableError(source, itinerary_id, reason=reason, hint=unavailable_hint)
