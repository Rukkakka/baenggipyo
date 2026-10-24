import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest
import typer

from baenggipyo.cli.app import (
    app,
    run,
)
from baenggipyo.cli.output import (
    emit,
    handle_errors,
)
from baenggipyo.core.errors import (
    AirportNotFoundError,
    BaenggipyoError,
)
from baenggipyo.core.flight import (
    Airport,
    Offer,
    check_place_inputs,
    pick_place,
    resolve_route,
    shown_offers,
)
from baenggipyo.core.http import raise_for_status
from baenggipyo.core.schema import built_from_response
from baenggipyo.providers.naver.api.request.common import NaverBookingItineraryModel


def stderr_error(capsys: pytest.CaptureFixture[str]) -> dict:
    captured = capsys.readouterr()
    assert captured.out == ''
    return json.loads(captured.err)['error']


# --- 사용법 오류 ---

@pytest.mark.parametrize(
    'argv',
    [
        ['nope'],                              # 없는 명령
        ['naver', 'nope'],                     # 없는 하위 명령
        ['limits', '--foo'],                   # 없는 옵션
        ['limits', '--reset', 'zigbang'],      # split_csv 의 BadParameter
        ['naver', 'search', 'flights', 'ICN', 'NRT', '--depart', '2020-01-01'],  # 지난 날짜
        ['naver', 'search', 'flights', 'ICN', 'NRT', '--depart', '2030-01-01', '--cabin', '3등석'],  # 없는 좌석 등급
    ],
)
def test_usage_errors_are_json_with_exit_2(
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_request(*args, **kwargs) -> None:
        raise AssertionError('입력 오류 검증에서 HTTP 요청을 보내면 안 됩니다')

    monkeypatch.setattr(httpx.Client, 'send', unexpected_request)
    assert run(argv) == 2
    error = stderr_error(capsys)
    assert error['code'] == 'INVALID_ARGUMENT'
    assert error['hint'].endswith("--help' 로 사용법을 확인하세요")


# --- 성공·에러 출력 ---

def test_success_exit_zero_and_json_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(['limits', '--compact']) == 0
    captured = capsys.readouterr()
    assert [source['source'] for source in json.loads(captured.out)['sources']] == ['naver', 'skyscanner']
    assert captured.err == ''


def test_limits_state_dir_failure_is_json_error(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 상태 디렉터리 자리에 파일이 있으면 만들 수 없다. traceback 대신 에러 JSON 이어야 한다
    blocker = tmp_path / 'not-a-dir'
    blocker.write_text('')
    monkeypatch.setenv('BAENGGIPYO_STATE_DIR', str(blocker))
    assert run(['limits']) == 1
    error = stderr_error(capsys)
    assert error['code'] == 'INTERNAL_ERROR'
    assert error['source'] == 'limits'


def test_keyboard_interrupt_is_json_error_with_exit_130(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Typer 는 KeyboardInterrupt 를 아무것도 쓰지 않고 Exit(130) 으로 바꾼다. stderr 가 비면 안 된다
    def interrupted(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr('baenggipyo.cli.app.state_dir', interrupted)
    assert run(['limits']) == 130
    error = stderr_error(capsys)
    assert error['code'] == 'INTERRUPTED'
    assert error['hint']


@pytest.mark.parametrize('exc,code,exit_code', [
    (json.JSONDecodeError('Expecting value', '<html>blocked</html>', 0), 'PARSE_ERROR', 1),
    (
        httpx.HTTPStatusError(
            '404',
            request=httpx.Request('GET', 'https://example.com'),
            response=httpx.Response(404),
        ),
        'NOT_FOUND',
        1,
    ),
    (BaenggipyoError('INVALID_ARGUMENT', '조회 중에야 안 입력 오류', '옵션을 고치세요'), 'INVALID_ARGUMENT', 2),
    # cp949 stdout(PYTHONUTF8 없는 Windows 파이프)에 결과를 쓰다 난 오류. ValueError 지만 응답 형식 변경(PARSE_ERROR)이 아니다
    (UnicodeEncodeError('cp949', '\u2014', 0, 1, 'illegal multibyte sequence'), 'INTERNAL_ERROR', 1),
])
def test_handle_errors_maps_exceptions_to_codes(
    exc: Exception,
    code: str,
    exit_code: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(typer.Exit) as exited, handle_errors('naver'):
        raise exc
    assert exited.value.exit_code == exit_code
    error = stderr_error(capsys)
    assert error['code'] == code and error['source'] == 'naver'


@pytest.mark.parametrize('body', [
    '<html><script>window.__s = {"sessionId": "secret-session"}</script></html>',  # 차단·오류 HTML 안의 JSON
    '{"traceId": "secret-session", "items": [1',  # 잘린 JSON
    '{"error": "{\\"requestId\\": \\"secret-session\\"}"}',  # JSON 값 안의 JSON 문자열
    '{"reason": "blocked", "redirect_to": "/sttc/px/captcha-v2/index.html?url=Lz8=&uuid=secret-session&vid=secret-session"}',  # 봇 차단의 캡차 주소
    '<html>captcha?uuid=secret-session&vid=secret-session</html>',  # 캡차 주소의 추적 ID
])
def test_upstream_error_message_hides_quoted_session_ids(body: str, capsys: pytest.CaptureFixture[str]) -> None:
    # 서버 오류 본문은 에러 JSON 의 message 로 나간다. JSON 으로 읽히지 않는 본문의 따옴표 친 키도 세션·추적 ID 를 가려야 한다
    # (AGENTS.md: 세션 토큰·추적 ID 는 출력하지 않는다). 오류 본문이라 실제 API 로 만들 수 없어 가짜 응답으로 본다
    response = httpx.Response(500, text=body, request=httpx.Request('GET', 'https://example.com/x'))
    with pytest.raises(typer.Exit), handle_errors('naver'):
        raise_for_status(response)
    error = stderr_error(capsys)
    assert error['code'] == 'UPSTREAM_ERROR'
    assert 'secret-session' not in error['message'] and '<redacted>' in error['message']


def test_request_built_from_response_fails_as_parse_error(capsys: pytest.CaptureFixture[str]) -> None:
    # 요청 모델 검증 오류는 입력 오류(exit 2)로 가르지만, 앞 응답의 값이 어긋나 난 것이면 입력을 고칠 일이 아니다.
    # 실제 응답은 날짜를 늘 줘서 가짜 값으로 본다
    with pytest.raises(typer.Exit) as exited, handle_errors('naver'):
        with built_from_response('네이버 판매처 조회'):
            NaverBookingItineraryModel(departure_airport='ICN', arrival_airport='NRT', departure_date='')
    assert exited.value.exit_code == 1 and stderr_error(capsys)['code'] == 'PARSE_ERROR'


def test_notes_come_first_and_vanish_when_empty(capsys: pytest.CaptureFixture[str]) -> None:
    emit({'returned': 0}, pretty=False, notes=['안내'])
    assert list(json.loads(capsys.readouterr().out)) == ['notes', 'returned']
    emit({'returned': 0}, pretty=False, notes=[])
    assert list(json.loads(capsys.readouterr().out)) == ['returned']


def test_four_letter_code_is_rejected_before_any_request() -> None:
    # 스카이스캐너 explore 의 sky_code 'CJUA'(제주) 자동완성은 청주·청두를 돌려준다 (2026-10-20 실측). 비슷한 후보를 권하면
    # 에이전트가 힌트대로 청주를 검색한다. 입력만 보고 알 수 있어 앞 구간 출발지의 해석 요청(스카이스캐너는 시간당 상한)도 보내지 않는다
    asked: list[str] = []
    with pytest.raises(AirportNotFoundError):
        resolve_route([('ICN', 'CJUA', '2026-11-01')], asked.append)
    assert asked == []
    # 대문자가 아니면 이름이다 (영어 도시 이름 Jeju·Rome). 후보가 하나면 그것을 쓴다
    check_place_inputs('Jeju', 'Rome')
    jeju = Airport(code='CJU', type='공항', name='제주국제공항', city='제주', country='대한민국')
    assert pick_place('Jeju', [jeju], lambda airport: airport) is jeju


# --- 출력 줄이기 ---

def test_offer_urls_count_fares_and_never_split_a_combination() -> None:
    # 따로 예약하는 조합(판매처마다 한 줄)은 두 편의 최저가 판매처가 다를 때만 와서 실제 조회로 재현하기 어렵다.
    # 링크가 한 줄에만 남으면 나머지 구간을 어디서 사는지 모른다. 뺀 링크는 모름(null)이 아니라 키가 없다
    fares = [
        [Offer(seller='한 곳', url='https://example.com/a')],
        [Offer(seller='가는 편', url='https://example.com/b1'), Offer(seller='오는 편', url='https://example.com/b2')],
        [Offer(seller='다른 곳', url='https://example.com/c')],
    ]

    def has_url(url_limit: int | None) -> list[bool]:
        return ['url' in row.model_dump(mode='json') for row in shown_offers(fares, 3, url_limit)]

    assert has_url(None) == [True, True, True, True]
    assert has_url(2) == [True, True, True, False]
    assert has_url(1) == [True, False, False, False]


# --- 매일 API 감시 ---

def test_every_source_command_is_in_api_monitor() -> None:
    # 비공식 API 는 언제 바뀔지 몰라 매일 새벽 소스 명령을 모두 실제로 불러 본다 (scripts/monitor/api_check.py).
    # 새 명령을 만들고 감시 목록에 넣지 않으면 그 명령은 깨져도 모른다
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'monitor' / 'api_check.py'
    spec = importlib.util.spec_from_file_location('api_check', path)
    assert spec is not None and spec.loader is not None
    monitor = importlib.util.module_from_spec(spec)
    sys.modules['api_check'] = monitor  # dataclass 가 자기 모듈을 찾는다
    spec.loader.exec_module(monitor)

    def leaves(command: object, path: tuple[str, ...]) -> list[tuple[str, ...]]:
        commands = getattr(command, 'commands', None)
        if not commands:
            return [path]
        return [leaf for name, sub in commands.items() for leaf in leaves(sub, (*path, name))]

    source_commands = {leaf for leaf in leaves(typer.main.get_command(app), ()) if leaf[0] in ('naver', 'skyscanner')}
    monitored = {step.command for steps in monitor.GROUPS.values() for step in steps}
    assert monitored == source_commands
