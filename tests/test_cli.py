import json

import httpx
import pytest
import typer

from baenggipyo.cli.app import run
from baenggipyo.cli.output import (
    emit,
    handle_errors,
)
from baenggipyo.core.errors import BaenggipyoError
from baenggipyo.core.flight import (
    NOTICE,
    Offer,
    shown_offers,
)
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
        ['naver', 'search', 'flights', 'ICN', 'NRT', '--depart', '2030-01-01', '--cabin', '이코노미'],  # 없는 좌석 등급
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


def test_request_built_from_response_fails_as_parse_error(capsys: pytest.CaptureFixture[str]) -> None:
    # 요청 모델 검증 오류는 입력 오류(exit 2)로 가르지만, 앞 응답의 값이 어긋나 난 것이면 입력을 고칠 일이 아니다.
    # 실제 응답은 날짜를 늘 줘서 가짜 값으로 본다
    with pytest.raises(typer.Exit) as exited, handle_errors('naver'):
        with built_from_response('네이버 판매처 조회'):
            NaverBookingItineraryModel(departure_airport='ICN', arrival_airport='NRT', departure_date='')
    assert exited.value.exit_code == 1 and stderr_error(capsys)['code'] == 'PARSE_ERROR'


def test_notes_sit_under_notice_and_vanish_when_empty(capsys: pytest.CaptureFixture[str]) -> None:
    emit({'returned': 0}, pretty=False, notice=True, notes=['안내'])
    payload = json.loads(capsys.readouterr().out)
    assert list(payload) == ['notice', 'notes', 'returned'] and payload['notice'] == NOTICE
    emit({'returned': 0}, pretty=False, notice=True, notes=[])
    assert list(json.loads(capsys.readouterr().out)) == ['notice', 'returned']


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
