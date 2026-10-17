"""스카이스캐너 CLI 명령을 접속 버릇(TLS·HTTP/2 지문)만 크롬처럼 바꿔 부른다 (매일 API 감시 전용, `api_check.py run --disguise`).

GitHub 러너는 데이터센터 IP 라 스카이스캐너 봇 차단에 첫 요청부터 막히곤 한다. 감시는 사이트가 바뀌어 코드가 깨졌는지 보려는 것이라
차단에 가려 확인하지 못하는 날을 줄이려고 감시에서만 전송을 curl_cffi 로 바꾼다. 설치되는 CLI 에는 넣지 않는다: CLI 는 쓰는 사람의
컴퓨터에서 있는 그대로 요청한다 (AGENTS.md '봇 차단을 우회하지 않는다' 는 CLI 원칙이고 검증용 감시는 예외다). 캡차는 풀지 않는다.
막히면 지금처럼 확인 못 함이다.

전송만 바꾼다. 요청 제한·로그 훅, 헤더, 응답 해석은 CLI 와 같다. 쿠키는 curl 쪽에 남기지 않는다.

    pip install curl_cffi==0.16.3
    python scripts/monitor/disguise.py --verbose skyscanner search flights ICN FUK --depart 2026-11-16
"""

import httpx
from curl_cffi import requests as curl_requests

from baenggipyo.cli.app import main
from baenggipyo.providers.skyscanner.api import client as skyscanner_client

from typing import Any


IMPERSONATE: str = 'chrome'
# curl 이 맞춰 넣는 헤더는 덮지 않는다. User-Agent 는 TLS 지문과 같은 크롬 판의 값을 curl_cffi 가 넣는다
CURL_REQUEST_HEADERS: frozenset[str] = frozenset({'user-agent', 'accept-encoding', 'connection', 'host', 'content-length'})
# curl 이 본문을 이미 풀어 준다. 그대로 두면 httpx 가 한 번 더 풀려다 깨진다
DECODED_RESPONSE_HEADERS: frozenset[str] = frozenset({'content-encoding', 'content-length', 'transfer-encoding'})


class CurlTransport(httpx.BaseTransport):
    """httpx 요청을 크롬 지문의 curl 로 보낸다."""

    def __init__(self) -> None:
        self.session = curl_requests.Session(impersonate=IMPERSONATE)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        # httpx 의 연결·읽기 시간 상한을 curl 에 그대로 넘긴다
        timeout = request.extensions.get('timeout') or {}
        connect, read = timeout.get('connect'), timeout.get('read')
        try:
            response = self.session.request(
                request.method,
                str(request.url),
                headers=[(key, value) for key, value in request.headers.multi_items() if key.lower() not in CURL_REQUEST_HEADERS],
                content=request.read() or None,
                timeout=(connect, read) if connect and read else read,
                allow_redirects=False,
                discard_cookies=True,
            )
        except curl_requests.exceptions.RequestException as exc:
            # CLI 의 네트워크 오류(NETWORK_ERROR)와 같게 보이도록 httpx 전송 오류로 바꾼다
            raise httpx.ConnectError(str(exc), request=request) from exc
        return httpx.Response(
            response.status_code,
            headers=[(key, value) for key, value in response.headers.multi_items() if key.lower() not in DECODED_RESPONSE_HEADERS],
            content=response.content,
            request=request,
        )

    def close(self) -> None:
        self.session.close()


def _client(*args: Any, **kwargs: Any) -> httpx.Client:
    return httpx.Client(*args, transport=CurlTransport(), **kwargs)


if __name__ == '__main__':
    # SkyscannerClient 가 httpx 클라이언트를 만드는 자리만 바꾼다. 인자(주소·헤더·요청 제한 훅·시간 상한)는 CLI 가 넘기는 그대로다
    skyscanner_client.Client = _client
    main()
