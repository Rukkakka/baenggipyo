import asyncio
import json
import math
import os
import sys
import time
from collections.abc import (
    Awaitable,
    Callable,
    Iterator,
)
from contextlib import contextmanager
from pathlib import Path

from httpx import (
    Request,
    Response,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from baenggipyo.core.errors import BaenggipyoError

from typing import (
    IO,
    Annotated,
    Any,
    TypeGuard,
    TypeVar,
)


if sys.platform == 'win32':
    import msvcrt
else:
    import fcntl


T = TypeVar('T')

WINDOW_SECONDS: float = 60.0
HOUR_SECONDS: float = 3600.0
# 거듭 차단될 때 쿨다운을 두 배씩 늘리는 횟수 상한 (2 ** n 이 넘치지 않게. 실제 길이는 max_cooldown 이 먼저 막는다)
MAX_BLOCK_STREAK: int = 20
# Windows 는 다른 프로세스가 열고 있는 파일을 replace 로 바꾸거나 바뀌는 중인 파일을 열면 잠깐 PermissionError 가 난다. 몇 번 다시 해 본다
SHARING_RETRIES: int = 5
SHARING_RETRY_SECONDS: float = 0.02


class RateLimitedError(BaenggipyoError):
    """요청을 보내기 전에 로컬 제한으로 거절했을 때.

    Args:
        source: 소스 이름 (naver, skyscanner).
        retry_after: 다시 시도할 수 있을 때까지 남은 초.
        reason: 'cooldown' (429·봇 차단 이후 대기 중), 'blocked' (방금 봇 차단을 받음) 또는 'budget' (분당·시간당 상한 초과).
        window: budget 일 때 넘은 상한 ('분당' 또는 '시간당'). Defaults to '분당'.
    """

    def __init__(self, source: str, retry_after: float, reason: str, window: str = '분당') -> None:
        seconds = max(int(retry_after + 0.999), 1)
        if reason == 'cooldown':
            message = f'{source} 가 요청을 막아(429 또는 봇 차단) 쿨다운 중입니다. {seconds}초 뒤 다시 시도하세요'
            hint = '쿨다운 중에는 요청을 보내지 않습니다. 그동안 다른 소스를 쓰세요'
        elif reason == 'blocked':
            message = f'{source} 가 봇 차단(캡차)으로 요청을 막았습니다. {seconds}초 동안 이 소스로 요청을 보내지 않습니다'
            hint = 'baenggipyo 는 차단을 우회하지 않습니다. 그동안 다른 소스를 쓰세요'
        elif window == '시간당':
            message = f'{source} 시간당 요청 상한에 도달했습니다. {seconds}초 뒤 다시 시도하세요'
            hint = '봇 차단을 피하려고 시간당 요청 수를 묶어 둔 것입니다. 기다리는 동안 다른 소스를 쓰세요'
        else:
            message = f'{source} 분당 요청 상한에 도달했습니다. {seconds}초 뒤 다시 시도하세요'
            hint = '조회 범위(--limit, 날짜 범위)를 줄이거나 잠시 뒤 다시 시도하세요'
        super().__init__(
            code='RATE_LIMITED',
            message=message,
            hint=hint,
            details={'retry_after_seconds': seconds, 'reason': reason},
        )
        self.retry_after = retry_after
        self.reason = reason


class RateLimitPolicy(BaseModel):
    """소스별 요청 제한 정책. 같은 소스를 쓰는 모든 프로세스가 공유한다.

    Args:
        min_interval: 요청 사이 최소 간격 (초).
        max_per_minute: 최근 60초 동안 허용하는 최대 요청 수. None 이면 제한 없음.
        max_per_hour: 최근 1시간 동안 허용하는 최대 요청 수. None 이면 제한 없음. 짧은 간격만 보는 상한으로는 막지 못하는 봇 차단용이다.
        cooldown: 429·봇 차단을 받은 뒤 요청을 보내지 않는 시간 (초). 응답에
            `Retry-After` 가 있으면 그 값과 이 값 중 큰 쪽을 쓴다.
        max_cooldown: 차단이 거듭될 때 쿨다운을 두 배씩 늘리는 상한 (초). None 이면 늘리지 않는다.
        max_wait: 한 요청이 슬롯을 기다릴 최대 시간 (초). 넘으면 기다리지 않고
            `RateLimitedError` 를 낸다.
        enabled: False 면 제한을 끈다.
    """

    model_config = ConfigDict(
        extra='forbid',
    )

    min_interval: Annotated[
        float,
        Field(ge=0, description='요청 사이 최소 간격 (초)')
    ] = 0.5
    max_per_minute: Annotated[
        int | None,
        Field(ge=1, description='최근 60초 동안 최대 요청 수')
    ] = None
    max_per_hour: Annotated[
        int | None,
        Field(ge=1, description='최근 1시간 동안 최대 요청 수')
    ] = None
    cooldown: Annotated[
        float,
        Field(ge=0, description='429·봇 차단 이후 요청을 멈추는 시간 (초)')
    ] = 300.0
    max_cooldown: Annotated[
        float | None,
        Field(ge=0, description='차단이 거듭될 때 두 배씩 늘린 쿨다운의 상한 (초). None 이면 늘리지 않는다')
    ] = None
    max_wait: Annotated[
        float,
        Field(ge=0, description='슬롯 대기 최대 시간 (초)')
    ] = 60.0
    enabled: Annotated[
        bool,
        Field(description='제한 사용 여부')
    ] = True


def _is_time(value: object) -> TypeGuard[float]:
    """상태 파일의 시각 값으로 쓸 수 있는지 (유한한 숫자). bool 과 NaN·무한대는 뺀다."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _absolute_env_path(name: str) -> Path | None:
    """환경 변수의 경로. `~` 는 펼치고, 비었거나 상대 경로면 None.

    상대 경로는 프로세스마다 작업 디렉터리 기준으로 풀려 프로세스끼리 상태 파일을 공유하지 못한다 (XDG 명세도 상대 경로는 무시한다).
    """
    value = os.environ.get(name)
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else None


def state_dir() -> Path:
    """상태 파일 디렉터리. `BAENGGIPYO_STATE_DIR` > `$XDG_CACHE_HOME/baenggipyo` > `~/.cache/baenggipyo`. 절대 경로가 아닌 값은 무시한다."""
    override = _absolute_env_path('BAENGGIPYO_STATE_DIR')
    if override:
        return override
    return (_absolute_env_path('XDG_CACHE_HOME') or Path.home() / '.cache') / 'baenggipyo'


def _retry_sharing(action: Callable[[], T]) -> T:
    """Windows 의 일시적인 공유 위반(`PermissionError`)이면 잠깐 쉬고 다시 한다. 계속 나면(권한이 정말 없음) 마지막 에러를 올린다."""
    for _ in range(SHARING_RETRIES - 1):
        try:
            return action()
        except PermissionError:
            time.sleep(SHARING_RETRY_SECONDS)
    return action()


def write_json_atomic(path: Path, data: object) -> None:
    """상태 디렉터리의 JSON 파일을 임시 파일에 쓴 뒤 `replace` 로 바꿔 넣는다. 읽는 쪽은 잠그지 않아도 늘 온전한 한 벌을 읽는다.

    임시 파일 이름에 프로세스 ID 를 넣어, 잠금 없이 쓰는 곳(판매처 이름표)에서 두 프로세스가 같은 임시 파일을 덮어쓰지 않게 한다. 쓰다 실패하면 임시 파일을 지우고 `OSError` 를 그대로 올린다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        _retry_sharing(lambda: temp.replace(path))
    finally:
        temp.unlink(missing_ok=True)


def _lock_file(file: IO[str]) -> None:
    """상태 파일 잠금을 잡는다. 다른 프로세스가 잡고 있으면 풀릴 때까지 기다린다."""
    if sys.platform == 'win32':
        # Windows 에는 fcntl 이 없다. 잠금 파일 첫 바이트를 잠그고, 실패하면 1초 간격으로 10번 다시 시도한 뒤 OSError 를 낸다.
        # 잠금은 슬롯을 예약하는 짧은 순간만 잡으므로 그 안에 풀린다
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(file, fcntl.LOCK_EX)


def _unlock_file(file: IO[str]) -> None:
    if sys.platform == 'win32':
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(file, fcntl.LOCK_UN)


class RateLimiter:
    """파일 기반 요청 제한기. 프로세스가 달라도 같은 소스는 같은 파일을 공유한다.

    상태 파일(`<state_dir>/ratelimit/<source>.json`)에 최근 요청 슬롯과 쿨다운
    종료 시각을 기록하고, 읽고 쓰는 동안 `flock`(Windows 는 `msvcrt.locking`)으로 잠근다. 잠금은 슬롯을
    예약하는 짧은 순간만 잡고, 실제 대기는 잠금 밖에서 한다. 대기가 끝나면
    쿨다운을 한 번 더 확인한다.

    Args:
        source: 소스 이름. 상태 파일 이름이 된다.
        policy: 제한 정책.
    """

    def __init__(self, source: str, policy: RateLimitPolicy) -> None:
        self.source = source
        self.policy = policy
        # `reserve_ahead` 로 미리 예약해 둔 다음 요청의 슬롯 시각 (epoch 초)
        self._ahead: float | None = None

    @property
    def path(self) -> Path:
        return state_dir() / 'ratelimit' / f'{self.source}.json'

    def _read_state(self) -> dict[str, Any]:
        """잠그지도 쓰지도 않고 상태를 읽는다. 쓰는 쪽은 임시 파일을 `replace` 로 바꿔 넣으므로 늘 온전한 한 벌을 읽는다.

        값이 숫자가 아닌 항목(손으로 고친 파일, 다른 버전이 쓴 파일 등)은 버린다. 상태 파일 하나 때문에 모든 요청과 `limits` 가 실패하지 않게 한다.
        읽을 수 없는 파일(UTF-8 이 아닌 내용, 읽기 권한 없음)도 빈 상태로 본다. 그래야 `limits --reset` 이 그 파일을 새로 써서 고칠 수 있다.
        다만 `PermissionError` 는 몇 번 다시 읽어 본다. Windows 에서는 다른 프로세스가 파일을 바꿔 넣는 순간에도 나는데, 그때 빈 상태로 보면
        방금 걸린 쿨다운을 놓치고 요청을 보낸다.
        경로가 잘못된 것(상태 디렉터리 자리에 파일, 상태 파일 자리에 디렉터리)은 다시 써도 고쳐지지 않아 그대로 올린다. 빈 상태로 보면 `limits` 가
        요청 기록 0 을 보여 주는데 요청은 모두 실패한다.
        돌려주는 상태에는 늘 `blocked_until`(float)·`recent`(float 목록)·`block_streak`(거듭 차단 횟수, 0 이상 정수)가 있다.
        """
        try:
            state = json.loads(_retry_sharing(lambda: self.path.read_text(encoding='utf-8')))
        except FileNotFoundError:
            # 경로 중간이 파일이면 macOS·Linux 는 NotADirectoryError 를 내지만 Windows 는 FileNotFoundError 를 내서 아직 기록이
            # 없는 것과 구분되지 않는다. 그 경로에는 상태를 쓸 수 없으니 macOS·Linux 처럼 그대로 올린다
            # (2026-10-14 CI windows-latest: 상태 디렉터리 자리에 파일이 있어도 limits 가 빈 상태로 exit 0)
            if any(parent.is_file() for parent in self.path.parents):
                raise
            state = {}
        except (PermissionError, ValueError):  # ValueError: JSONDecodeError·UnicodeDecodeError
            state = {}
        if not isinstance(state, dict):
            state = {}
        blocked_until = state.get('blocked_until')
        recent = state.get('recent')
        streak = state.get('block_streak')
        return {
            'blocked_until': float(blocked_until) if _is_time(blocked_until) else 0.0,
            'recent': [float(slot) for slot in recent if _is_time(slot)] if isinstance(recent, list) else [],
            'block_streak': streak if isinstance(streak, int) and not isinstance(streak, bool) and streak >= 0 else 0,
        }

    @property
    def window(self) -> float:
        """슬롯을 남겨 두는 시간 (초). 시간당 상한이 있으면 1시간, 없으면 60초."""
        return HOUR_SECONDS if self.policy.max_per_hour is not None else WINDOW_SECONDS

    def _live_slots(self, state: dict[str, Any], now: float) -> list[float]:
        """최근 `window`(60초, 시간당 상한이 있으면 1시간) 안의 슬롯 (오름차순).

        예약한 슬롯은 아무리 늦어도 예약한 때부터 `max_wait` 안이다. 그보다 먼 슬롯은 시계가 뒤로 간 흔적이라 버린다. 남겨 두면 그 시간만큼 모든
        요청이 budget 으로 거절된다. 정책이 다른 프로세스의 예약을 지우지 않게 60초보다 짧게 보지는 않는다. 쿨다운(`blocked_until`)은 그대로 둔다.
        뒤로 간 만큼 길어질 뿐이고, 봇 차단 뒤에는 길게 쉬는 쪽이 안전하다.
        """
        horizon = now + max(self.policy.max_wait, WINDOW_SECONDS)
        return sorted(slot for slot in state['recent'] if now - self.window < slot <= horizon)

    @contextmanager
    def _locked_state(self) -> Iterator[dict[str, Any]]:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path.with_suffix('.lock'), 'a+') as lock:
            _lock_file(lock)
            try:
                state = self._read_state()
                yield state
                write_json_atomic(path, state)
            finally:
                _unlock_file(lock)

    def _check_cooldown(self) -> None:
        """쿨다운 중이면 `RateLimitedError` 를 낸다 (읽기만 한다)."""
        now = time.time()
        blocked_until = self._read_state()['blocked_until']
        if blocked_until > now:
            raise RateLimitedError(self.source, blocked_until - now, 'cooldown')

    def _next_slot(self, state: dict[str, Any], now: float, max_wait: float | None = None) -> tuple[list[float], float]:
        """다음 요청을 보낼 시각과 최근 슬롯(`_live_slots`)을 돌려준다. 상태를 바꾸지 않는다.

        Raises:
            RateLimitedError: 쿨다운 중이거나, 슬롯까지 `max_wait`(정책 값과 인자 중 짧은 쪽)보다 오래 기다려야 할 때.
        """
        blocked_until = state['blocked_until']
        if blocked_until > now:
            raise RateLimitedError(self.source, blocked_until - now, 'cooldown')

        recent = self._live_slots(state, now)
        minute = [slot for slot in recent if slot > now - WINDOW_SECONDS]
        slot = now
        if recent:
            slot = max(slot, recent[-1] + self.policy.min_interval)
        limit = self.policy.max_per_minute
        if limit is not None and len(minute) >= limit:
            slot = max(slot, minute[-limit] + WINDOW_SECONDS)
        hourly = self.policy.max_per_hour
        hour_slot = recent[-hourly] + HOUR_SECONDS if hourly is not None and len(recent) >= hourly else 0.0

        delay = max(slot, hour_slot) - now
        if delay > (self.policy.max_wait if max_wait is None else min(max_wait, self.policy.max_wait)):
            raise RateLimitedError(self.source, delay, 'budget', '시간당' if hour_slot > slot else '분당')
        return recent, max(slot, hour_slot)

    def reserve(self, max_wait: float | None = None) -> float:
        """다음 요청 슬롯을 예약하고 그때까지 기다릴 초를 돌려준다.

        Args:
            max_wait: 정책의 `max_wait` 보다 짧게 기다릴 때 그 초. None 이면 정책 값.

        Raises:
            RateLimitedError: 쿨다운 중이거나, 슬롯까지 `max_wait` 보다 오래
                기다려야 할 때. 이 경우 슬롯을 예약하지 않는다.
        """
        if not self.policy.enabled:
            return 0.0
        with self._locked_state() as state:
            now = time.time()
            recent, slot = self._next_slot(state, now, max_wait)
            recent.append(slot)
            state['recent'] = recent
            return slot - now

    def reserve_ahead(self, within: float | None = None) -> None:
        """다음 요청의 슬롯을 지금 예약해 둔다. 그 요청의 `acquire` 는 새로 예약하지 않고 이 슬롯까지 기다린다.

        앞 응답을 놓기 전에 다음 요청의 자리를 잡아 둘 때 쓴다 (스카이스캐너 검색 폴링). 놓은 뒤에 예약하다 거절되면 받은 결과를 잃는다.
        예약한 슬롯은 같은 인스턴스의 다음 요청 하나가 쓴다. 그 요청이 재시도하면 재시도는 새로 예약한다.

        Args:
            within: 슬롯이 이 초보다 멀면 예약하지 않는다 (폴링 시간 상한까지 남은 시간). None 이면 `max_wait`.

        Raises:
            RateLimitedError: 지금 `reserve` 가 거절되거나 슬롯이 `within` 초보다 멀 때. 이때는 아무것도 예약하지 않는다.
        """
        if self.policy.enabled:
            self._ahead = time.time() + self.reserve(within)

    def _take_slot(self) -> tuple[float, bool]:
        """이번 요청이 기다릴 초와, `reserve_ahead` 로 미리 잡아 둔 슬롯을 썼는지. 잡아 둔 슬롯이 없으면 새로 예약한다."""
        if self._ahead is None:
            return self.reserve(), False
        slot, self._ahead = self._ahead, None
        return slot - time.time(), True

    def acquire(self) -> None:
        delay, ahead = self._take_slot()
        if delay > 0:
            time.sleep(delay)
        if delay > 0 or ahead:
            # 기다리는 동안(미리 잡은 슬롯은 잡은 뒤로) 다른 프로세스가 429·봇 차단으로 쿨다운을 걸었을 수 있다. 그러면 예약한 슬롯도 쓰지 않는다
            self._check_cooldown()

    async def aacquire(self) -> None:
        delay, ahead = self._take_slot()
        if delay > 0:
            await asyncio.sleep(delay)
        if delay > 0 or ahead:
            self._check_cooldown()

    def block(self, retry_after: float | None = None) -> float:
        """429·봇 차단을 받았을 때 쿨다운을 건다. 이미 더 긴 쿨다운이 있으면 유지한다.

        `max_cooldown` 이 있으면 차단이 거듭될 때마다 쿨다운을 두 배로 늘린다 (그 상한까지). 앞 쿨다운이 끝나고 `max_cooldown` 안에 다시 막히면
        거듭된 것으로 보고, 그보다 오래 막히지 않았으면 처음 길이로 돌아간다. 쿨다운 중에 들어온 차단(다른 프로세스가 같이 받은 것)은 늘리지 않는다.
        차단 중에는 쿨다운이 끝날 때마다 보내는 첫 요청이 다시 막히므로, 길게 쉬어 그런 요청을 줄인다.

        Returns:
            float: 쿨다운 종료 시각 (epoch 초).
        """
        if not self.policy.enabled:
            return 0.0
        with self._locked_state() as state:
            now = time.time()
            previous = state['blocked_until']
            streak = state['block_streak']
            if self.policy.max_cooldown is None:
                streak = 0
            elif previous <= now:
                streak = min(streak + 1, MAX_BLOCK_STREAK) if previous and now - previous < self.policy.max_cooldown else 0
            cooldown = self.policy.cooldown * 2 ** streak
            if self.policy.max_cooldown is not None:
                cooldown = min(cooldown, max(self.policy.max_cooldown, self.policy.cooldown))
            blocked_until = max(previous, now + max(cooldown, retry_after or 0.0))
            state['blocked_until'] = blocked_until
            state['block_streak'] = streak
            return blocked_until

    def reset(self) -> None:
        """쿨다운과 요청 기록을 지운다."""
        with self._locked_state() as state:
            state.clear()

    def status(self) -> dict[str, Any]:
        """현재 쿨다운·최근 60초(시간당 상한이 있으면 1시간도) 요청 수를 돌려준다. 읽기만 해서 잠그지 않고, 상태 디렉터리에 쓸 권한이 없어도 된다."""
        state = self._read_state()
        now = time.time()
        blocked_until = state['blocked_until']
        recent = self._live_slots(state, now)
        status: dict[str, Any] = {
            'source': self.source,
            'cooldown_remaining_seconds': max(int(blocked_until - now + 0.999), 0),
            'requests_last_minute': sum(1 for slot in recent if slot > now - WINDOW_SECONDS),
        }
        if self.policy.max_per_hour is not None:
            # 시간당 상한이 없으면 1시간치 기록을 남기지 않아 셀 수 없다 (해당 없음이라 키를 뺀다)
            status['requests_last_hour'] = len(recent)
        status['policy'] = self.policy.model_dump()
        return status

    def _retry_after(self, response: Response) -> float | None:
        # core.http 의 파서를 쓰되 순환 import 를 피하려고 여기서 가져온다.
        from baenggipyo.core.http import retry_after_seconds

        header = response.headers.get('Retry-After')
        return retry_after_seconds(header) if header else None

    def on_rate_limited(self, response: Response) -> None:
        """429 응답이면 쿨다운을 걸고 곧바로 `RateLimitedError` 를 낸다.

        쿨다운(최소 15분)이 재시도 대기 상한(60초)보다 길어 재시도는 어차피 로컬에서
        거절된다. `HTTPStatusError` 로 두면 tenacity 가 `Retry-After` 만큼 헛되이
        기다리므로, 재시도 대상이 아닌 에러로 바로 끝낸다. 제한을 끈 정책이면
        아무것도 하지 않아 기존 429 재시도가 그대로 동작한다.
        """
        if response.status_code != 429 or not self.policy.enabled:
            return
        blocked_until = self.block(self._retry_after(response))
        raise RateLimitedError(self.source, blocked_until - time.time(), 'cooldown')

    def event_hooks(self) -> dict[str, list[Callable[..., None]]]:
        """sync httpx `Client` 용 event hooks: 요청 전 슬롯 대기, 429 응답 시 쿨다운."""
        def on_request(request: Request) -> None:
            self.acquire()

        def on_response(response: Response) -> None:
            self.on_rate_limited(response)

        return {'request': [on_request], 'response': [on_response]}

    def async_event_hooks(self) -> dict[str, list[Callable[..., Awaitable[None]]]]:
        """`AsyncClient` 용 event hooks. 동작은 `event_hooks` 와 같다."""
        async def on_request(request: Request) -> None:
            await self.aacquire()

        async def on_response(response: Response) -> None:
            self.on_rate_limited(response)

        return {'request': [on_request], 'response': [on_response]}
