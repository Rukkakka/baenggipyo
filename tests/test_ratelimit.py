import multiprocessing
import time
from pathlib import Path

import httpx
import pytest

from baenggipyo.core.http import BaseHTTPClient
from baenggipyo.core.ratelimit import (
    RateLimitedError,
    RateLimiter,
    RateLimitPolicy,
)
from baenggipyo.providers.naver.api.client import NaverClient
from baenggipyo.providers.skyscanner.api.client import SkyscannerClient


def limiter(**policy: object) -> RateLimiter:
    return RateLimiter('test', RateLimitPolicy(**policy))


def test_min_interval_spaces_reservations(monkeypatch: pytest.MonkeyPatch) -> None:
    # 시계를 고정한다. 실제 시계로 재면 느린 러너(windows-latest)에서 예약 사이에 흐른 시간만큼 어긋난다
    monkeypatch.setattr(time, 'time', lambda: 1000.0)
    first, second = limiter(min_interval=1.0), limiter(min_interval=1.0)
    # 다른 인스턴스(= 다른 프로세스)라도 같은 상태 파일을 공유한다
    assert first.reserve() == 0
    assert second.reserve() == 1.0
    assert first.reserve() == 2.0


def _reserve_many(count: int) -> None:
    worker = RateLimiter('test', RateLimitPolicy(min_interval=0))
    for _ in range(count):
        worker.reserve()


def test_concurrent_processes_keep_every_reservation() -> None:
    # 여러 baenggipyo 프로세스가 동시에 예약해도 상태 파일을 잠가 예약이 사라지지 않는다.
    # Windows 는 fcntl 이 없어 msvcrt 로 잠근다 (잠그지 않던 때는 동시 실행에서 서로의 예약을 덮어썼다)
    context = multiprocessing.get_context('spawn')
    workers = [context.Process(target=_reserve_many, args=(30,)) for _ in range(3)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(60)
    assert [worker.exitcode for worker in workers] == [0, 0, 0]
    assert limiter().status()['requests_last_minute'] == 90


def test_per_minute_budget_refuses_long_waits() -> None:
    budget = limiter(min_interval=0, max_per_minute=2, max_wait=5)
    budget.reserve()
    budget.reserve()
    with pytest.raises(RateLimitedError) as exc:
        budget.reserve()
    assert exc.value.reason == 'budget'
    assert exc.value.details['retry_after_seconds'] >= 55


def test_hourly_budget_refuses_when_hour_is_full() -> None:
    # 분당 상한으로는 막지 못하는 수십 분에 걸친 누적을 막는 상한. 실제 API 로 재면 차단을 받는다
    hourly = limiter(min_interval=0, max_per_minute=10, max_per_hour=2, max_wait=5)
    hourly.reserve()
    hourly.reserve()
    with pytest.raises(RateLimitedError) as exc:
        hourly.reserve()
    assert exc.value.reason == 'budget'
    assert '시간당' in exc.value.message
    assert exc.value.details['retry_after_seconds'] >= 3500
    # limits 와 스카이스캐너 결과 notes 가 남은 요청과 한 칸이 비는 때를 같은 셈으로 알린다
    status = hourly.status()
    assert status['requests_remaining_hour'] == 0 and status['hour_slot_wait_seconds'] >= 3500


def test_repeated_blocks_double_cooldown_up_to_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    # 쿨다운이 끝나고 곧 다시 막히면 두 배로 쉬고(상한까지), 오래 막히지 않았으면 처음 길이로 돌아간다. 실제 차단으로는 재현하지 않는다
    now = [1000.0]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    blocked = limiter(cooldown=60, max_cooldown=200)
    lengths = []
    # 앞 쿨다운이 끝나고 gap 초 뒤에 다시 막힌다
    for gap in (0, 10, 10, 300):
        now[0] += blocked.status()['cooldown_remaining_seconds'] + gap
        lengths.append(blocked.block() - now[0])
    assert lengths == [60, 120, 200, 60]


def test_cooldown_blocks_until_expiry() -> None:
    cooled = limiter(min_interval=0, cooldown=60)
    cooled.block()
    with pytest.raises(RateLimitedError) as exc:
        cooled.reserve()
    assert exc.value.reason == 'cooldown'
    assert 55 <= exc.value.retry_after <= 60


def test_cooldown_set_while_waiting_stops_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    # 슬롯을 기다리는 동안 다른 프로세스가 봇 차단으로 쿨다운을 걸면, 깨어난 요청도 보내지 않는다 (실제 API 로는 재현하기 어려운 경합)
    waiting, other = limiter(min_interval=5, cooldown=60), limiter(min_interval=5, cooldown=60)
    waiting.acquire()
    monkeypatch.setattr(time, 'sleep', lambda seconds: other.block())
    with pytest.raises(RateLimitedError) as exc:
        waiting.acquire()
    assert exc.value.reason == 'cooldown'


def test_slot_reserved_ahead_is_taken_by_next_request_only() -> None:
    # 스카이스캐너 폴링은 앞 응답을 놓기 전에 다음 요청의 슬롯을 잡아 둔다. 그 사이 다른 프로세스가 그 슬롯을 가져가지 못하고,
    # 잡아 둔 요청은 슬롯을 두 번 쓰지 않는다 (실제 API 로는 재현하기 어려운 경합)
    ahead, other = limiter(min_interval=0, max_per_minute=1, max_wait=0), limiter(min_interval=0, max_per_minute=1, max_wait=0)
    ahead.reserve_ahead()
    with pytest.raises(RateLimitedError):
        other.reserve()
    ahead.acquire()
    assert ahead.status()['requests_last_minute'] == 1
    with pytest.raises(RateLimitedError):
        ahead.acquire()


def test_slot_ahead_past_poll_deadline_is_not_reserved() -> None:
    # 폴링은 시간 상한까지 남은 시간 안의 슬롯만 미리 잡는다. 더 먼 슬롯은 예약하지 않고 거절해, 쓰지 않을 슬롯이 상한을 깎지 않게 한다
    # (분당 상한 근처의 상태라 실제 API 로 맞추려면 요청 예산을 일부러 써야 한다)
    ahead = limiter(min_interval=0, max_per_minute=1, max_wait=60)
    ahead.reserve()
    with pytest.raises(RateLimitedError):
        ahead.reserve_ahead(within=10)
    assert ahead.status()['requests_last_minute'] == 1


def test_transient_permission_error_does_not_drop_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    # Windows 는 다른 프로세스가 상태 파일을 바꿔 넣는 순간 읽으면 PermissionError 가 잠깐 난다. 빈 상태로 보면 걸린 쿨다운을 놓치고
    # 그 빈 상태를 다시 써서 쿨다운을 지운다. macOS·Linux 에서는 나지 않아 한 번 실패하는 읽기로 본다
    cooled = limiter(min_interval=0, cooldown=60)
    cooled.block()
    read_text = Path.read_text
    failures = [PermissionError('sharing violation')]

    def flaky_read(path: Path, encoding: str | None = None, errors: str | None = None) -> str:
        if path == cooled.path and failures:
            raise failures.pop()
        return read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, 'read_text', flaky_read)
    with pytest.raises(RateLimitedError) as exc:
        cooled.acquire()
    assert exc.value.reason == 'cooldown' and not failures


def test_retry_after_longer_than_cooldown_wins() -> None:
    cooled = limiter(cooldown=10)
    until = cooled.block(retry_after=120)
    assert until - time.time() == pytest.approx(120, abs=1)


def test_block_never_shortens_existing_cooldown() -> None:
    cooled = limiter(cooldown=10)
    long_until = cooled.block(retry_after=300)
    assert cooled.block() == long_until


def test_disabled_policy_is_noop() -> None:
    off = limiter(enabled=False, min_interval=10)
    off.block()
    assert off.reserve() == 0


@pytest.mark.parametrize('client_class', [NaverClient, SkyscannerClient])
def test_429_sets_cooldown_and_next_call_never_reaches_server(client_class: type[BaseHTTPClient]) -> None:
    # 모든 provider client 가 요청 제한 훅을 거치는지 본다 (엔드포인트가 없어 경로를 직접 부른다)
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(429, json={})

    client = client_class(rate_limit=RateLimitPolicy(min_interval=0, cooldown=900))
    transport = httpx.MockTransport(handler)
    real_client = client._client
    client.__dict__['_client'] = httpx.Client(
        base_url=real_client.base_url,
        transport=transport,
        event_hooks=real_client.event_hooks,
    )
    real_client.close()

    with client:
        with pytest.raises(RateLimitedError) as exc:
            client._client.get('/')
        assert exc.value.reason == 'cooldown' and exc.value.retry_after > 800
        with pytest.raises(RateLimitedError):
            client._client.get('/')

    assert len(sent) == 1
    # 새 클라이언트(= 다음 CLI 실행)도 같은 쿨다운을 본다
    assert client_class().rate_limiter.status()['cooldown_remaining_seconds'] > 800


def test_429_with_disabled_policy_stays_http_error() -> None:
    off = limiter(enabled=False)
    off.on_rate_limited(httpx.Response(429))  # 제한을 끄면 그대로 둔다 (재시도에 맡김)
