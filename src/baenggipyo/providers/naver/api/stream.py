"""검색 SSE 스트림에서 마지막 이벤트를 꺼낸다.

`searchFlights` 는 판매처 하나가 끝날 때마다 그때까지의 결과 전체를 `data:` 한 줄로 다시 보낸다. 앞 이벤트는 뒤
이벤트에 모두 들어 있으므로 마지막 줄만 쓰면 된다 (편도 limit 200 은 이벤트 20개 27MB, 2026-10-11 실측).
스트림을 중간에 끊으면 서버가 검색을 버리므로(바로 뒤 캐시 조회가 isExpired) 시간 상한에 닿을 때만 끊는다.

스트림 읽기·줄 나누기·시간 상한은 브라우저 JS(`core/browser.py` `FETCH_SCRIPT`)가 한다. 응답 헤더가 온 때부터
`STREAM_SECONDS_EXTENSION` 초가 지나거나 다음 조각이 client `timeout` 안에 오지 않으면 그때까지 받은 마지막 줄로 끝내고,
받은 줄이 없으면 transport 가 `ReadTimeout` 을 낸다. 읽다 연결이 끊기면 받은 줄을 쓰지 않고 전송 오류로 올려 `naver_retry` 가
검색을 처음부터 한 번 더 한다. 끊긴 스트림의 마지막 이벤트를 쓰면 판매처 일부가 빠진 결과를 '시간 상한' 안내로 잘못 설명하게 되고,
끊는 순간 서버도 그 검색을 버려 `flight` 의 캐시 조회가 만료로 온다 (2026-10-11 리뷰에서 확인).

파이썬 `iter_lines` 는 돌려받은 마지막 이벤트 한 줄을 다시 `str.splitlines` 처럼 U+2028·U+2029·U+0085 등에서 가르므로, JSON 문자열
값에 그런 문자가 있으면 `data:` 줄이 잘린다. 네이버 응답에는 없어 그대로 둔다 (2026-11-01 ICN→BKK·ICN→CDG 검색 원문 약 1,400만 자에 0개).
"""

from collections.abc import (
    AsyncIterator,
    Iterator,
)


DATA_PREFIX: str = 'data:'


def last_event(lines: Iterator[str]) -> str | None:
    """`data:` 줄 중 마지막 것의 본문을 돌려준다. 이벤트가 없으면 None.

    Args:
        lines: 응답 본문의 줄.
    """
    # 줄 하나가 수 MB 라 이벤트마다 접두어를 떼어 복사하지 않는다. 마지막 줄만 들고 있다가 끝에 한 번 뗀다
    last: str | None = None
    for line in lines:
        if line.startswith(DATA_PREFIX):
            last = line
    return last[len(DATA_PREFIX):] if last is not None else None


async def alast_event(lines: AsyncIterator[str]) -> str | None:
    """Async variant of `last_event`."""
    last: str | None = None
    async for line in lines:
        if line.startswith(DATA_PREFIX):
            last = line
    return last[len(DATA_PREFIX):] if last is not None else None
