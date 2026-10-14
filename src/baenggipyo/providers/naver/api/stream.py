"""검색 SSE 스트림에서 마지막 이벤트를 꺼낸다.

`searchFlights` 는 판매처 하나가 끝날 때마다 그때까지의 결과 전체를 `data:` 한 줄로 다시 보낸다. 앞 이벤트는 뒤
이벤트에 모두 들어 있으므로 마지막 줄만 남기고 버린다 (편도 limit 200 은 이벤트 20개 27MB, 2026-10-11 실측).
스트림을 중간에 끊으면 서버가 검색을 버리므로(바로 뒤 캐시 조회가 isExpired) 시간 상한에 닿을 때만 끊는다.

시간 상한은 두 가지다. 전체 시간(`deadline`)은 줄이 올 때마다 보고, 줄 사이 간격은 httpx 의 읽기 시간 상한(client
`timeout`, 기본 20초)이 막는다. 늦은 판매처 때문에 다음 줄이 그 안에 오지 않아도 이미 받은 이벤트가 있으면 버리지 않고
그것으로 끝낸다 (이벤트 간격은 길어야 5초였다, 2026-10-11 실측 ICN→NRT 20곳).

받은 이벤트를 남기는 것은 읽기 시간 상한(`ReadTimeout`)뿐이고, 연결이 끊긴 것 같은 다른 전송 오류는 그대로 올려 `naver_retry` 가
검색을 처음부터 한 번 더 한다. 일부러 그렇게 둔다: 읽기 시간 상한은 늦은 판매처 때문이라 다시 해도 또 늦지만, 끊긴 연결은 대개 일시적이라
다시 하면 끝까지 받는다. 끊긴 스트림의 마지막 이벤트를 쓰면 판매처 일부가 빠진 결과를 '시간 상한' 안내로 잘못 설명하게 되고, 끊는 순간
서버도 그 검색을 버려 `flight` 의 캐시 조회가 만료로 온다 (2026-10-11 리뷰에서 확인).
"""

import time
from collections.abc import (
    AsyncIterator,
    Iterator,
)

from httpx import ReadTimeout


DATA_PREFIX: str = 'data:'


def last_event(lines: Iterator[str], deadline: float | None = None) -> str | None:
    """`data:` 줄 중 마지막 것의 본문을 돌려준다. 이벤트가 없으면 None.

    Args:
        lines: 응답 본문의 줄.
        deadline: `time.monotonic()` 기준 마감 시각. 지나면 그때까지 받은 마지막 이벤트로 끝낸다.

    Raises:
        ReadTimeout: 이벤트를 하나도 받기 전에 읽기 시간 상한에 닿았을 때 (받은 뒤면 그 이벤트로 끝낸다).
    """
    # 줄 하나가 수 MB 라 이벤트마다 접두어를 떼어 복사하지 않는다. 마지막 줄만 들고 있다가 끝에 한 번 뗀다
    last: str | None = None
    try:
        for line in lines:
            if line.startswith(DATA_PREFIX):
                last = line
            if deadline is not None and time.monotonic() > deadline:
                break
    except ReadTimeout:
        if last is None:
            raise
    return last[len(DATA_PREFIX):] if last is not None else None


async def alast_event(lines: AsyncIterator[str], deadline: float | None = None) -> str | None:
    """Async variant of `last_event`."""
    last: str | None = None
    try:
        async for line in lines:
            if line.startswith(DATA_PREFIX):
                last = line
            if deadline is not None and time.monotonic() > deadline:
                break
    except ReadTimeout:
        if last is None:
            raise
    return last[len(DATA_PREFIX):] if last is not None else None
