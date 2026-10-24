"""두 소스의 모든 CLI 명령을 실제로 불러 비공식 API 가 깨졌는지 본다 (매일 새벽 `.github/workflows/api-monitor.yml`).

명령마다 셋으로 나눈다 (references/development.md '매일 API 감시').

- 정상: exit 0 이고 결과가 1건 이상
- 고장: 응답 형식 오류·서버 거절(4xx·500)·내부 오류 같은 에러, 결과 0건, JSON 이 아닌 출력. 인기 노선이라 0건이면 사이트가 바뀐 것으로 본다.
  출발 시각대 조건을 건 검색은 그 시각대 밖 항공권이 섞여도 고장이다 (네이버가 필터를 말없이 무시하게 된 것)
- 확인 못 함: 봇 차단·요청 제한·네트워크 오류·서버 일시 오류(502·503 등)·검색 시간 초과·검색 직후 매진, 판매처가 모두 빠진 상세.
  사이트가 바뀐 것인지 알 수 없다

    python scripts/monitor/api_check.py run naver --out result-naver.json
    python scripts/monitor/api_check.py report result-*.json [--issues] [--dry-run]

새 CLI 명령을 만들면 GROUPS 에 넣는다 (국제선·국내선이 다르게 동작하면 둘 다). tests/test_cli.py 가 CLI 명령 목록과 대조한다.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Step:
    """감시할 명령 하나.

    Args:
        label: 결과 표에 쓰는 이름.
        command: CLI 명령 경로 (예: `('naver', 'search', 'flights')`).
        args: 인자. `{depart}`(오늘부터 DAYS_AHEAD 일 뒤), `{return}`(그 TRIP_DAYS 일 뒤), `{month}`(출발 달), `{id}`(바로 앞 검색 결과의
            첫 항공권 ID), `{return_id}`(바로 앞 네이버 국내선 왕복 검색의 오는 편 첫 ID)를 채운다.
        hours: `--depart-time`·`--return-time` 으로 건 출발 시각대 (시작 시, 끝 시). 차례로 첫·둘째 구간이고, 결과가 모두 그 안이어야 정상이다.
    """

    label: str
    command: tuple[str, ...]
    args: tuple[str, ...]
    hours: tuple[tuple[int, int], ...] = ()


DAYS_AHEAD: int = 30
# 왕복 검색의 여행 일수
TRIP_DAYS: int = 3
# 스카이스캐너 검색은 폴링 60초에 요청 제한 대기(최대 60초)가 붙을 수 있다
COMMAND_TIMEOUT_SECONDS: int = 300
SEARCH: tuple[str, ...] = ('--depart', '{depart}', '--limit', '2', '--brief')
FLIGHT: tuple[str, ...] = ('{id}', '--limit', '3', '--offer-urls', '0')
# 네이버 국내선 왕복은 가는 편·오는 편을 따로 찾아(`legs[]`) 상세도 두 편 ID 로 왕복 예약 가격을 묻는다
ROUND_TRIP_SEARCH: tuple[str, ...] = ('--depart', '{depart}', '--return', '{return}', '--limit', '2', '--brief')
ROUND_TRIP_FLIGHT: tuple[str, ...] = ('{id}', '{return_id}', '--limit', '3', '--offer-urls', '0')
# `run --disguise` 면 스카이스캐너 명령을 이것으로 부른다 (접속 버릇만 크롬처럼, curl_cffi 필요)
DISGUISE: Path = Path(__file__).with_name('disguise.py')

# 러너 하나가 묶음 하나를 돈다. 스카이스캐너 명령을 모두 부르면 요청이 40번 남짓이라 한 러너로는 시간당 상한(30회)에 막혀 한 시간 넘게
# 기다리므로 국제선·국내선을 나눈다 (한 묶음 15~25번). 막힌 묶음을 다른 러너로 다시 돌리지는 않는다 (막힌 날은 확인 못 함으로 남긴다).
# 왕복은 네이버 국내선만 부른다. 네이버 국내선 왕복만 결과 모양(`legs[]`)과 요청(오는 편 검색, 두 편 예약 가격)이 편도와 다르다. 스카이스캐너
# 국내선 왕복은 결과가 8천 건·100MB 를 넘는다. 노선은 결과가 0건일 리 없는 인기 노선으로 고른다 (0건이면 고장으로 본다)
GROUPS: dict[str, tuple[Step, ...]] = {
    'naver': (
        Step('공항 찾기', ('naver', 'search', 'airports'), ('인천',)),
        Step('국제선 검색 인천→도쿄', ('naver', 'search', 'flights'), ('ICN', 'NRT', *SEARCH)),
        Step('국제선 상세', ('naver', 'flight'), FLIGHT),
        Step('국내선 검색 김포→제주', ('naver', 'search', 'flights'), ('GMP', 'CJU', *SEARCH)),
        Step('국내선 상세', ('naver', 'flight'), FLIGHT),
        Step('국내선 왕복 검색 김포↔제주', ('naver', 'search', 'flights'), ('GMP', 'CJU', *ROUND_TRIP_SEARCH)),
        Step('국내선 왕복 상세', ('naver', 'flight'), ROUND_TRIP_FLIGHT),
        # 출발 시각대는 네이버 서버 필터다 (국제선 filter.departureTime, 국내선 filter.segment__departure__time). 필터 키가 바뀌어 무시되면
        # 결과는 그대로 나와 위 검색으로는 모른다. 스카이스캐너는 받은 결과를 baenggipyo 가 걸러 API 변화와 상관없어 부르지 않는다
        Step('국제선 검색 출발 06~12시 인천→도쿄', ('naver', 'search', 'flights'),
             ('ICN', 'NRT', '--depart', '{depart}', '--depart-time', '06-12', '--limit', '5', '--brief'), hours=((6, 12),)),
        Step('국내선 왕복 검색 가는 편 06~12시·오는 편 18~24시', ('naver', 'search', 'flights'),
             ('GMP', 'CJU', *ROUND_TRIP_SEARCH, '--depart-time', '06-12', '--return-time', '18-24'), hours=((6, 12), (18, 24))),
        Step('국제선 달력 인천→도쿄', ('naver', 'calendar'), ('ICN', 'NRT', '--month', '{month}')),
        Step('국내선 달력 김포→제주', ('naver', 'calendar'), ('GMP', 'CJU', '--month', '{month}')),
        # 네이버 어디든지는 국제선 목적지만 준다
        Step('어디든지 인천 출발', ('naver', 'explore'), ('ICN', '--limit', '5')),
    ),
    'skyscanner-international': (
        Step('공항 찾기', ('skyscanner', 'search', 'airports'), ('인천',)),
        Step('국제선 달력 인천→후쿠오카', ('skyscanner', 'calendar'), ('ICN', 'FUK', '--month', '{month}')),
        # 스카이스캐너 어디든지는 국내·국제 도시가 함께 나온다
        Step('어디든지 인천 출발', ('skyscanner', 'explore'), ('ICN', '--limit', '5')),
        Step('국제선 검색 인천→후쿠오카', ('skyscanner', 'search', 'flights'), ('ICN', 'FUK', *SEARCH)),
        Step('국제선 상세', ('skyscanner', 'flight'), FLIGHT),
    ),
    'skyscanner-domestic': (
        Step('국내선 달력 김포→제주', ('skyscanner', 'calendar'), ('GMP', 'CJU', '--month', '{month}')),
        Step('국내선 검색 김포→제주', ('skyscanner', 'search', 'flights'), ('GMP', 'CJU', *SEARCH)),
        Step('국내선 상세', ('skyscanner', 'flight'), FLIGHT),
    ),
}

OK: str = 'ok'
BROKEN: str = 'broken'
UNKNOWN: str = 'unknown'
STATUS_LABELS: dict[str, str] = {OK: '✅ 정상', BROKEN: '❌ 고장', UNKNOWN: '⚠️ 확인 못 함'}
# 사이트가 바뀐 것인지 알 수 없는 에러. 그 밖의 에러 코드(PARSE_ERROR, INTERNAL_ERROR, NOT_FOUND, AIRPORT_NOT_FOUND 등)는 고장이다
UNKNOWN_CODES: frozenset[str] = frozenset({'RATE_LIMITED', 'NETWORK_ERROR', 'FARE_UNAVAILABLE'})
# 결과 수를 셀 키. 네이버 국내선 왕복 검색만 `legs[].itineraries` 다
COUNT_KEYS: tuple[str, ...] = ('offers', 'itineraries', 'airports', 'days', 'destinations')

BROKEN_LABEL: str = 'api-broken'
BLOCKED_LABEL: str = 'api-blocked'
# 같은 묶음이 이만큼 연속으로 확인 못 함이면 알린다 (오늘 포함)
BLOCKED_STREAK: int = 3
WORKFLOW_FILE: str = 'api-monitor.yml'
REPORT_ARTIFACT: str = 'report'
# 날짜는 CLI 처럼 한국 시간으로 센다. 러너는 UTC 라 그대로 쓰면 한국 04:17 실행이 전날로 찍힌다. baenggipyo 의 KST·today_kst 를 가져오지 않는
# 것은 결과 정리(report) 잡이 baenggipyo 를 설치하지 않고 표준 라이브러리만으로 돌아서다 (CLI 는 조회 잡에서 python -m baenggipyo 로 부른다)
KST: datetime.timezone = datetime.timezone(datetime.timedelta(hours=9), 'KST')


def _today() -> datetime.date:
    return datetime.datetime.now(KST).date()


# --- 조회 ---

def _error(stderr: str) -> dict[str, object] | None:
    """stderr 의 에러 JSON (`--verbose` 로그 뒤 마지막 `{` 줄)."""
    for line in reversed(stderr.splitlines()):
        if line.startswith('{'):
            try:
                body = json.loads(line).get('error')
            except ValueError:
                return None
            return body if isinstance(body, dict) else None
    return None


def _is_unknown(error: dict[str, object]) -> bool:
    code = error.get('code')
    if code in UNKNOWN_CODES:
        return True
    if code != 'UPSTREAM_ERROR':
        return False
    # 검색이 시간 상한에 끊긴 것과 서버 일시 오류(502·503 등)는 사이트가 바뀐 것이 아니다. 4xx 는 요청 형식이 맞지 않게 된 것이라 고장이다.
    # 500 도 고장이다. 두 소스 모두 요청 형식이 맞지 않으면 늘 같은 500 을 준다 (각 api/client.py 설명. 네이버는 헤더가 빠지면, 스카이스캐너
    # 달력은 도시를 SEL 로 주면). 사이트가 잠깐 아파 500 을 준 날에는 고장으로 잘못 알릴 수 있다
    status = error.get('status')
    return error.get('reason') == 'SEARCH_INCOMPLETE' or (isinstance(status, int) and status > 500)


def _directions(data: dict[str, object]) -> list[list[object]]:
    """검색 결과의 항공권을 방향마다. 네이버 국내선 왕복 검색만 `legs[].itineraries` 로 가는 편·오는 편을 나눠 주고, 다른 검색은 방향 하나다."""
    itineraries = data.get('itineraries')
    if isinstance(itineraries, list):
        return [itineraries]
    legs = data.get('legs')
    if not isinstance(legs, list):
        return []
    directions: list[list[object]] = []
    for leg in legs:
        items = leg.get('itineraries') if isinstance(leg, dict) else None
        directions.append(items if isinstance(items, list) else [])
    return directions


def _count(data: dict[str, object]) -> int:
    for key in COUNT_KEYS:
        value = data.get(key)
        if isinstance(value, list):
            return len(value)
    return sum(len(items) for items in _directions(data))


def _first_id(directions: list[list[object]], index: int) -> str:
    """`index` 번째 방향(0 가는 편, 1 오는 편)의 첫 항공권 ID. 그 방향이 없거나 비었으면 빈 문자열.

    방향을 섞어 고르면 가는 편이 0건일 때 오는 편 ID 가 `{id}`·`{return_id}` 둘 다에 들어가 상세가 같은 방향 두 편(INVALID_ARGUMENT)으로 고장 난다.
    """
    items = directions[index] if index < len(directions) else []
    first = items[0] if items else None
    value = first.get('id') if isinstance(first, dict) else None
    return value if isinstance(value, str) else ''


def _outside_hours(data: dict[str, object], hours: tuple[tuple[int, int], ...]) -> int:
    """출발 시각대 밖 항공권 수. 방향이 둘(네이버 국내선 왕복 `legs[]`)이면 방향마다, 하나면 항공권의 여정(`journeys`)마다 차례로 맞춘다.

    끝 시 정각(12:00) 출발은 밖으로 세지 않는다. 네이버 칸의 끝 시각 포함 여부는 정각 출발 편이 없어 재지 못했다 (references/providers/naver.md).
    """
    directions = _directions(data)
    outside = 0
    for index, items in enumerate(directions):
        for item in items:
            journeys = item.get('journeys') if isinstance(item, dict) else None
            for order, journey in enumerate(journeys if isinstance(journeys, list) else []):
                position = index if len(directions) > 1 else order
                departure = journey.get('departure_at') if isinstance(journey, dict) else None
                if position >= len(hours) or not isinstance(departure, str) or len(departure) < 16:
                    continue
                start, end = hours[position]
                hour, minute = int(departure[11:13]), int(departure[14:16])
                if not (start <= hour < end or (hour == end and minute == 0)):
                    outside += 1
    return outside


def _run_step(step: Step, values: dict[str, str], *, disguise: bool = False) -> tuple[str, str, dict[str, object] | None]:
    """(판정, 내용, stdout JSON). `disguise` 면 스카이스캐너 명령을 disguise.py 로 부른다."""
    command = [*step.command, *(arg.format(**values) for arg in step.args)]
    entry = [str(DISGUISE)] if disguise and step.command[0] == 'skyscanner' else ['-m', 'baenggipyo']
    print(f"::group::baenggipyo {' '.join(command)}{' (disguise)' if entry[0] == str(DISGUISE) else ''}", flush=True)
    try:
        done = subprocess.run(
            [sys.executable, *entry, '--verbose', *command],
            capture_output=True, text=True, encoding='utf-8', timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print('::endgroup::', flush=True)
        return UNKNOWN, f'{COMMAND_TIMEOUT_SECONDS}초 안에 끝나지 않음', None
    print(done.stderr.strip())
    print(done.stdout.strip()[:2000])
    print('::endgroup::', flush=True)
    if done.returncode == 0:
        try:
            data = json.loads(done.stdout)
        except ValueError:
            return BROKEN, 'stdout 이 JSON 이 아님', None
        if not isinstance(data, dict):
            return BROKEN, 'stdout 이 JSON 객체가 아님', None
        count = _count(data)
        outside = _outside_hours(data, step.hours) if step.hours else 0
        if outside:
            return BROKEN, f'출발 시각대 밖 항공권 {outside}건 (시간 필터가 무시됨)', data
        if count:
            return OK, f'{count}건', data
        notes = data.get('notes')
        if data.get('offers') == [] and isinstance(notes, list) and notes:
            # 상세는 판매처를 모두 뺀 까닭(실시간 가격을 주지 않는 판매처 등)을 notes 로 알리고 offers 를 비워 정상으로 끝날 수 있다
            return UNKNOWN, f'판매처 운임 0건: {" / ".join(str(note) for note in notes)[:200]}', data
        return BROKEN, '결과 0건', data
    error = _error(done.stderr)
    if error is None:
        return BROKEN, f'exit {done.returncode}, 에러 JSON 없음', None
    detail = ' '.join(str(part) for part in (error.get('code'), error.get('reason'), error.get('status')) if part)
    message = str(error.get('message') or '').replace('\n', ' ')
    return (UNKNOWN if _is_unknown(error) else BROKEN), f'{detail}: {message[:200]}', None


def run_group(name: str, *, disguise: bool = False) -> dict[str, object]:
    """묶음 하나의 명령을 차례로 부른다. 상세는 바로 앞 검색 결과의 첫 항공권 ID 로 부르고, 그 검색이 실패했으면 건너뛴다."""
    depart = _today() + datetime.timedelta(days=DAYS_AHEAD)
    values = {
        'depart': depart.isoformat(),
        'return': (depart + datetime.timedelta(days=TRIP_DAYS)).isoformat(),
        'month': depart.strftime('%Y-%m'),
        'id': '',
        'return_id': '',
    }
    steps: list[dict[str, str]] = []
    for step in GROUPS[name]:
        line = ' '.join(step.command)
        if any(f'{{{key}}}' in step.args and not values[key] for key in ('id', 'return_id')):
            steps.append({'label': step.label, 'command': line, 'status': UNKNOWN, 'detail': '앞 검색이 결과를 주지 않아 건너뜀'})
            continue
        status, detail, data = _run_step(step, values, disguise=disguise)
        if step.command[-1] == 'flights':
            directions = _directions(data) if data else []
            values['id'] = _first_id(directions, 0)
            values['return_id'] = _first_id(directions, 1)
        steps.append({'label': step.label, 'command': line, 'status': status, 'detail': detail})
    return {'group': name, 'date': _today().isoformat(), 'status': _overall([step['status'] for step in steps]), 'steps': steps}


def _overall(statuses: list[str]) -> str:
    if BROKEN in statuses:
        return BROKEN
    return UNKNOWN if UNKNOWN in statuses else OK


# --- 결과 정리 ---

def _table(results: list[dict]) -> str:
    rows = ['| 묶음 | 명령 | 결과 | 내용 |', '| --- | --- | --- | --- |']
    for result in results:
        for step in result['steps']:
            detail = step['detail'].replace('|', '\\|')
            rows.append(f"| {result['group']} | {step['label']} (`{step['command']}`) | {STATUS_LABELS[step['status']]} | {detail} |")
    return '\n'.join(rows)


def _run_link(text: str = '실행 기록') -> str:
    server, repository, run_id = (os.environ.get(key) for key in ('GITHUB_SERVER_URL', 'GITHUB_REPOSITORY', 'GITHUB_RUN_ID'))
    return f' ([{text}]({server}/{repository}/actions/runs/{run_id}))' if server and repository and run_id else ''


def _gh(*args: str, write: bool = False, dry_run: bool = False) -> str:
    """gh CLI. `dry_run` 이면 이슈·라벨을 바꾸는 명령은 보내지 않고 출력만 한다."""
    if write and dry_run:
        print('[dry-run] gh', ' '.join(args))
        return ''
    done = subprocess.run(['gh', *args], capture_output=True, text=True, encoding='utf-8')
    if done.returncode != 0:
        # 권한 부족·라벨 생성 실패 같은 원인이 로그에 남게 stderr 를 찍는다. 이슈 본문이 길어 명령은 앞 두 낱말만
        print(f"gh {' '.join(args[:2])} 실패 (exit {done.returncode}): {done.stderr.strip()}", file=sys.stderr)
        done.check_returncode()
    return done.stdout


def _open_issue(label: str) -> int | None:
    issues = json.loads(_gh('issue', 'list', '--label', label, '--state', 'open', '--json', 'number', '--limit', '1'))
    return issues[0]['number'] if issues else None


def _run_date(created_at: str) -> datetime.date:
    """실행 시작 시각(gh 의 `createdAt`, UTC ISO 8601)의 한국 날짜."""
    return datetime.datetime.fromisoformat(created_at.replace('Z', '+00:00')).astimezone(KST).date()


def _previous_reports(count: int) -> list[dict]:
    """어제부터 하루에 하나씩 거슬러 올라간 묶음별 판정 (그날 마지막 예약·수동 실행). 같은 브랜치의 실행만 본다.

    실행 횟수가 아니라 날짜로 센다. 하루에 여러 번 돌린 날도 하루다. 실행이 없는 날(예약 실행이 빠짐)이나 결과 파일이 없는 실행(만료·실패)을
    만나면 거기서 멈춘다.
    """
    command = ['run', 'list', '--workflow', WORKFLOW_FILE, '--status', 'completed', '--limit', '30', '--json', 'databaseId,event,createdAt']
    branch = os.environ.get('GITHUB_REF_NAME')
    if branch:
        command += ['--branch', branch]
    try:
        runs = json.loads(_gh(*command))
    except subprocess.CalledProcessError:
        # 워크플로가 아직 기본 브랜치에 없으면(첫 실행 전) 실행 목록을 묻지 못한다
        return []
    current = os.environ.get('GITHUB_RUN_ID')
    expected = _today() - datetime.timedelta(days=1)
    reports: list[dict] = []
    # gh run list 는 최근 실행부터 준다
    for run in runs:
        if len(reports) == count:
            break
        if run['event'] not in ('schedule', 'workflow_dispatch') or str(run['databaseId']) == current:
            continue
        day = _run_date(run['createdAt'])
        if day > expected:
            # 오늘 앞서 돌린 실행이거나 이미 센 날의 더 이른 실행
            continue
        if day < expected:
            break
        with tempfile.TemporaryDirectory() as folder:
            try:
                _gh('run', 'download', str(run['databaseId']), '--name', REPORT_ARTIFACT, '--dir', folder)
            except subprocess.CalledProcessError:
                break
            reports.append(json.loads((Path(folder) / 'report.json').read_text(encoding='utf-8')))
        expected -= datetime.timedelta(days=1)
    return reports


def _update_issues(results: list[dict], dry_run: bool) -> None:
    """고장은 api-broken, 사흘 연속 확인 못 함은 api-blocked 이슈로 알린다. 다시 모두 괜찮아지면 닫는다."""
    table, link = _table(results), _run_link()
    statuses = {result['group']: result['status'] for result in results}
    broken = [group for group, status in statuses.items() if status == BROKEN]
    _gh('label', 'create', BROKEN_LABEL, '--color', 'd73a4a', '--description', '매일 API 감시가 찾은 고장', '--force', write=True, dry_run=dry_run)
    _gh('label', 'create', BLOCKED_LABEL, '--color', 'fbca04', '--description', '매일 API 감시가 며칠째 확인하지 못함', '--force', write=True, dry_run=dry_run)

    number = _open_issue(BROKEN_LABEL)
    if broken:
        body = f'{_today()} 감시에서 고장: {", ".join(broken)}{link}\n\n{table}'
        if number is None:
            _gh('issue', 'create', '--label', BROKEN_LABEL, '--title', f'API 감시: 고장 ({", ".join(broken)})', '--body', body,
                write=True, dry_run=dry_run)
        else:
            _gh('issue', 'comment', str(number), '--body', body, write=True, dry_run=dry_run)
    elif number is not None and all(status == OK for status in statuses.values()):
        _gh('issue', 'close', str(number), '--comment', f'{_today()} 감시에서 모두 정상이다{link}', write=True, dry_run=dry_run)

    # 어제부터 거슬러 BLOCKED_STREAK 일. 오늘 막 연속이 된 묶음(어제까지는 연속이 아니었다)을 가리려고 하루 더 받는다
    previous = [{result['group']: result['status'] for result in report['results']} for report in _previous_reports(BLOCKED_STREAK)]

    def unknown_before(group: str, days: int) -> bool:
        return len(previous) >= days and all(report.get(group) == UNKNOWN for report in previous[:days])

    streak = [group for group, status in statuses.items() if status == UNKNOWN and unknown_before(group, BLOCKED_STREAK - 1)]
    started = [group for group in streak if not unknown_before(group, BLOCKED_STREAK)]
    number = _open_issue(BLOCKED_LABEL)
    if streak and number is None:
        body = (f'{BLOCKED_STREAK}일 연속 확인 못 함: {", ".join(streak)}{_run_link("오늘 실행 기록")}. 봇 차단·요청 제한·서버 일시 오류로 '
                f'고장인지 알 수 없는 상태가 이어진다. 막힘이 계속되면 그 묶음을 덜 자주 돌리는 것을 검토한다.\n\n{table}')
        _gh('issue', 'create', '--label', BLOCKED_LABEL, '--title', f'API 감시: {BLOCKED_STREAK}일째 확인 못 함 ({", ".join(streak)})',
            '--body', body, write=True, dry_run=dry_run)
    elif started and number is not None:
        # 이슈가 열린 뒤 다른 묶음도 연속 확인 못 함이 됐다
        _gh('issue', 'comment', str(number), '--body', f'{_today()} 감시에서 {BLOCKED_STREAK}일 연속 확인 못 함: {", ".join(started)}{link}\n\n{table}',
            write=True, dry_run=dry_run)
    elif number is not None and not streak and (UNKNOWN not in statuses.values() or len(previous) >= BLOCKED_STREAK - 1):
        # 오늘 확인 못 함이 남았어도 연속이 끊겼으면 닫는다. 앞선 날 결과를 받지 못해 연속인지 모르면 닫지 않는다
        _gh('issue', 'close', str(number), '--comment', f'{_today()} 감시에서 {BLOCKED_STREAK}일 연속 확인 못 함인 묶음이 없다{link}',
            write=True, dry_run=dry_run)


def report(paths: list[str], *, issues: bool, dry_run: bool) -> int:
    """묶음별 결과 파일을 모아 표를 남기고, 고장이 있으면 1 을 돌려준다 (실행이 실패로 끝나 GitHub 이 메일로 알린다)."""
    found = {}
    for path in paths:
        # 결과 파일이 하나도 없으면 셸이 글롭(result-*.json)을 그대로 넘긴다
        if not Path(path).is_file():
            continue
        result = json.loads(Path(path).read_text(encoding='utf-8'))
        found[result['group']] = result
    # 결과 파일이 없는 묶음은 조회 잡이 감시 코드나 설치에서 실패한 것이다
    results = [
        found.get(group) or {'group': group, 'status': BROKEN, 'steps': [{'label': '조회 잡', 'command': '-', 'status': BROKEN, 'detail': '결과 파일 없음 (조회 잡 실패)'}]}
        for group in GROUPS
    ]
    text = f'## API 감시 {_today()}\n\n{_table(results)}\n'
    print(text)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as file:
            file.write(text)
    Path('report.json').write_text(json.dumps({'results': results}, ensure_ascii=False), encoding='utf-8')
    if issues:
        _update_issues(results, dry_run)
    return 1 if any(result['status'] == BROKEN for result in results) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='action', required=True)
    run = commands.add_parser('run', help='묶음 하나의 명령을 부르고 결과를 JSON 파일로 쓴다')
    run.add_argument('group', choices=list(GROUPS))
    run.add_argument('--out', required=True)
    run.add_argument('--disguise', action='store_true', help='스카이스캐너 명령을 접속 버릇만 크롬처럼 바꿔 부른다 (disguise.py, curl_cffi 필요)')
    collect = commands.add_parser('report', help='결과 파일을 모아 표를 남기고 이슈를 고친다')
    collect.add_argument('paths', nargs='*')
    collect.add_argument('--issues', action='store_true', help='고장·연속 확인 못 함을 이슈로 알린다 (예약·수동 실행만)')
    collect.add_argument('--dry-run', action='store_true', help='이슈·라벨을 바꾸지 않고 보낼 gh 명령만 출력한다')
    options = parser.parse_args()
    if options.action == 'run':
        result = run_group(options.group, disguise=options.disguise)
        Path(options.out).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        print(_table([result]))
        return 0
    return report(options.paths, issues=options.issues, dry_run=options.dry_run)


if __name__ == '__main__':
    sys.exit(main())
