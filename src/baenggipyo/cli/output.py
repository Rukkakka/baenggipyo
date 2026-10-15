import json
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import (
    date,
    datetime,
)

import typer
from httpx import (
    HTTPError,
    HTTPStatusError,
)
from pydantic import (
    BaseModel,
    ValidationError,
)
from typer._click.exceptions import (
    ClickException,  # Typer 가 내장한 Click 의 예외. 비공개 경로라 Typer 업그레이드 시 확인 필요.
)
from typer._click.globals import get_current_context

from baenggipyo.core.errors import BaenggipyoError
from baenggipyo.core.flight import (
    KST,
    NOTICE,
)
from baenggipyo.core.schema import is_request_validation_error

from typing import (
    Any,
    Literal,
    TypeVar,
)


ChoiceT = TypeVar('ChoiceT', bound=str)

# 도움말 모양 (최상위 Typer 의 rich_markup_mode, 하위 명령도 따른다). 터미널이면 Rich 상자로, 파이프(LLM 에이전트)면 Click 평문으로 쓴다.
# 파이프의 Rich 상자는 80칸 안에 테두리·여백을 넣느라 같은 도움말이 두 배 넘게 길고, 좁은 칸에서 설명을 … 로 자른다 (2026-10-15 실측)
HELP_MARKUP_MODE: Literal['rich'] | None = 'rich' if sys.stdout.isatty() else None

DATE_INPUT_PATTERN: re.Pattern[str] = re.compile(r'^\d{4}-\d{2}-\d{2}$')
MONTH_INPUT_PATTERN: re.Pattern[str] = re.compile(r'^\d{4}-\d{2}$')
LEG_INPUT_PATTERN: re.Pattern[str] = re.compile(r'^(?P<origin>[^-\s]+)-(?P<destination>[^-\s]+)-(?P<date>\d{4}-\d{2}-\d{2})$')
AIRLINE_CODE_PATTERN: re.Pattern[str] = re.compile(r'^[A-Z0-9]{2}$')
# CLI 가 받는 승객 합계 상한 (validate_passengers). 소스 요청 모델의 유형별 상한(api/request/common.py)과는 따로 둔다
MAX_PASSENGERS: int = 9


def _dumps(data: Any, pretty: bool | None) -> str:
    if pretty is None:
        pretty = sys.stdout.isatty()
    return json.dumps(
        data,
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (',', ':'),
    )


def emit(
    data: BaseModel | list[BaseModel] | dict[str, Any],
    pretty: bool | None = None,
    notice: bool = False,
    notes: list[str] | None = None,
) -> None:
    """결과를 JSON 으로 stdout 에 쓴다.

    값을 모르는 필드는 `null` 로 남기고, 그 항공권에 해당 없는 필드는 출력 모델이 키째 뺀다.

    `notice` 가 True 면 맨 앞에 "가격은 조회 시점 기준, 예약은 원 사이트에서" 안내를 붙인다.
    항공권·가격을 내보내는 명령은 모두 켠다. `notes` 가 있으면 그 바로 아래에 붙이고,
    없으면 키를 뺀다 (모호한 공항 안내, 일부만 돌려준 안내 등).
    """
    if isinstance(data, BaseModel):
        payload: Any = data.model_dump(mode='json')
    elif isinstance(data, list):
        payload = [item.model_dump(mode='json') for item in data]
    else:
        payload = data
    if not isinstance(payload, dict) and (notice or notes):
        payload = {'items': payload}
    if notes:
        payload = {'notes': notes, **payload}
    if notice:
        payload = {'notice': NOTICE, **payload}
    typer.echo(_dumps(payload, pretty))


def emit_error(code: str, message: str, hint: str | None = None, **extra: Any) -> None:
    """`{"error": {...}}` 를 stderr 에 쓴다. 터미널이면 들여쓰기, 아니면 한 줄."""
    error: dict[str, Any] = {'code': code, 'message': message}
    if hint:
        error['hint'] = hint
    error.update({key: value for key, value in extra.items() if value is not None})
    # 들여쓰기는 stdout 이 아니라 에러를 쓰는 stderr 가 터미널인지로 정한다 (`baenggipyo ... | jq` 도 에러는 터미널에 보인다)
    typer.echo(_dumps({'error': error}, sys.stderr.isatty()), err=True)


def _validation_message(error: Any) -> str:
    """요청 모델 검증 오류 문구. 검증 함수가 던진 ValueError 는 pydantic 이 붙이는 'Value error, ' 를 뗀다."""
    if error.get('type') == 'value_error' and 'error' in error.get('ctx', {}):
        return str(error['ctx']['error'])
    return error['msg']


def _usage_hint() -> str:
    """사용법 오류(baenggipyo.cli.app.run)와 같은 '<명령> --help' 힌트. 명령 밖이면 일반 문구."""
    context = get_current_context(silent=True)
    if context is None:
        return '옵션 값을 확인하세요'
    return f"'{context.command_path} --help' 로 사용법을 확인하세요"


@contextmanager
def handle_errors(source: str) -> Iterator[None]:
    """예외를 `{"error": {...}}` JSON 으로 stderr 에 쓰고 exit code 1 (입력 검증 실패는 2) 로 끝낸다.

    LLM 이 실패 원인을 읽고 다음 행동(재시도, 조건 수정)을 고를 수 있도록
    코드와 힌트를 함께 남긴다. 예상하지 못한 예외도 traceback 대신 INTERNAL_ERROR 로 쓴다.
    """
    try:
        yield
    except BaenggipyoError as exc:
        # 다른 소스 때문에 난 에러는 details 의 source 가 이긴다
        emit_error(exc.code, exc.message, exc.hint, **{'source': source, **exc.details})
        # 조회 중에야 알 수 있는 입력 오류도 사용법 오류와 같은 exit code 2 로 맞춘다
        raise typer.Exit(code=2 if exc.code == 'INVALID_ARGUMENT' else 1)
    except ValidationError as exc:
        if not is_request_validation_error(exc):
            # 응답 모델 검증 실패는 소스 응답이 바뀐 것이다. 입력을 고쳐도 소용없다.
            emit_error(
                'PARSE_ERROR',
                f'{exc.title} 검증 실패: ' + '; '.join(error['msg'] for error in exc.errors()),
                '비공식 API 응답 형식이 바뀌었을 수 있습니다. 다른 소스를 쓰세요',
                source=source,
            )
            raise typer.Exit(code=1)
        emit_error(
            'INVALID_ARGUMENT',
            '; '.join(_validation_message(error) for error in exc.errors()),
            _usage_hint(),
            source=source,
        )
        # 사용법 오류와 같은 종류라 exit code 도 2 로 맞춘다.
        raise typer.Exit(code=2)
    except HTTPStatusError as exc:
        status = exc.response.status_code
        code = {
            404: 'NOT_FOUND',
            429: 'RATE_LIMITED',
        }.get(status, 'UPSTREAM_ERROR')
        # 요청 제한이 켜져 있으면 429 는 응답 훅이 쿨다운을 걸고 RateLimitedError 로 바꾼다. 여기 오는 429 는 제한을 끈
        # 정책(RateLimitPolicy(enabled=False))이라 쿨다운이 없으므로, 막아 두었다고 안내하지 않는다
        hint = {
            404: 'ID·공항 코드가 맞는지, 검색 결과가 만료되지 않았는지 확인하세요',
            429: '요청이 너무 잦습니다. 잠시 기다린 뒤 다시 시도하거나 그사이 다른 소스를 쓰세요',
        }.get(status, '비공식 API 라 스펙이 바뀌었을 수 있습니다')
        emit_error(code, str(exc), hint, source=source, status=status)
        raise typer.Exit(code=1)
    except HTTPError as exc:
        emit_error(
            'NETWORK_ERROR',
            f'{type(exc).__name__}: {exc}',
            '네트워크 상태를 확인하고 다시 시도하세요',
            source=source,
        )
        raise typer.Exit(code=1)
    except UnicodeEncodeError as exc:
        # 결과를 쓰다 난 것이다 (Windows 파이프의 cp949 처럼 출력 인코딩이 UTF-8 이 아님). ValueError 라 아래로 가면 응답 형식이 바뀐
        # PARSE_ERROR 로 보여 에이전트가 소스를 바꾼다. 메시지의 문자는 — 처럼 ASCII 로 나와 같은 인코딩의 stderr 에도 쓸 수 있다
        emit_error(
            'INTERNAL_ERROR',
            f'출력 인코딩({exc.encoding})으로 쓸 수 없는 문자가 있어 결과를 쓰지 못했습니다: {exc}',
            '환경 변수 PYTHONUTF8=1 을 설정하고 다시 실행하세요 (README 의 Windows 에서 쓸 때)',
            source=source,
        )
        raise typer.Exit(code=1)
    except (ValueError, LookupError) as exc:
        # JSON 이 아닌 응답(차단 페이지 등)의 JSONDecodeError 포함
        emit_error(
            'PARSE_ERROR',
            f'{type(exc).__name__}: {exc}',
            '응답이 예상한 형식이 아닙니다. 차단 페이지이거나 구조가 바뀌었을 수 있으니 다른 소스를 쓰세요',
            source=source,
        )
        raise typer.Exit(code=1)
    except (ClickException, typer.Exit):
        # 사용법 오류와 정상 종료는 baenggipyo.cli.app.run 이 처리한다
        raise
    except BrokenPipeError:
        # 읽는 쪽이 먼저 끝난 것이다 (`| head`). 내부 오류가 아니라 Typer 가 출력을 조용히 닫고 exit 1 로 끝낸다
        raise
    except Exception as exc:
        emit_error(
            'INTERNAL_ERROR',
            f'{type(exc).__name__}: {exc}',
            'baenggipyo 내부 오류입니다. 다른 소스를 쓰거나 baenggipyo --verbose <명령> 처럼 baenggipyo 바로 뒤에 --verbose 를 붙여 다시 실행해 보세요',
            source=source,
        )
        raise typer.Exit(code=1)


def split_csv(values: list[str] | None, allowed: tuple[ChoiceT, ...], option: str) -> list[ChoiceT]:
    """`--cabin 일반석 --cabin 비즈니스석` 과 `--cabin 일반석,비즈니스석` 을 모두 받아 검증된 목록으로 만든다.

    값의 타입은 `allowed` 를 따른다.
    """
    result: list[ChoiceT] = []
    for value in values or []:
        for part in value.split(','):
            part = part.strip()
            if not part:
                continue
            if part not in allowed:
                raise typer.BadParameter(
                    f"허용되지 않는 값입니다: '{part}'. 가능한 값: {', '.join(allowed)}",
                    param_hint=option,
                )
            if part not in result:
                result.append(part)
    return result


def one_of(value: str, allowed: tuple[ChoiceT, ...], option: str) -> ChoiceT:
    """값 하나를 허용값으로 검증한다. 쉼표로 여러 개를 주면 사용법 오류다."""
    values = split_csv([value], allowed, option)
    if len(values) != 1:
        raise typer.BadParameter(f"값 하나만 주세요. 가능한 값: {', '.join(allowed)}", param_hint=option)
    return values[0]


def today_kst() -> date:
    return datetime.now(KST).date()


def parse_date(value: str, option: str) -> str:
    """`YYYY-MM-DD` 를 검증한다. 없는 날짜와 오늘(한국 날짜)보다 이른 날짜는 사용법 오류다."""
    value = value.strip()
    if DATE_INPUT_PATTERN.fullmatch(value) is None:
        raise typer.BadParameter(f'날짜는 YYYY-MM-DD 로 주세요: {value}', param_hint=option)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter(f'없는 날짜입니다: {value}', param_hint=option) from None
    if parsed < today_kst():
        raise typer.BadParameter(f'지난 날짜입니다: {value} (오늘 {today_kst().isoformat()})', param_hint=option)
    return value


def parse_month(value: str | None, option: str) -> str:
    """`YYYY-MM` 을 검증한다. 주지 않으면 이번 달(한국 날짜), 지난 달은 사용법 오류다."""
    this_month = today_kst().isoformat()[:7]
    if value is None:
        return this_month
    value = value.strip()
    if MONTH_INPUT_PATTERN.fullmatch(value) is None or not 1 <= int(value[5:]) <= 12:
        raise typer.BadParameter(f'달은 YYYY-MM 으로 주세요: {value}', param_hint=option)
    if value < this_month:
        raise typer.BadParameter(f'지난 달입니다: {value} (이번 달 {this_month})', param_hint=option)
    return value


def parse_legs(
    origin: str | None,
    destination: str | None,
    depart: str | None,
    return_: str | None,
    leg: list[str] | None,
) -> list[tuple[str, str, str]]:
    """노선 입력(출발·도착 인자 + --depart/--return, 또는 --leg 반복)을 (출발, 도착, YYYY-MM-DD) 구간들로 바꾸고 검사한다."""
    if leg:
        if origin or destination or depart or return_:
            raise typer.BadParameter('--leg 는 출발·도착 인자, --depart, --return 과 함께 쓰지 않습니다', param_hint='--leg')
        legs = []
        for value in leg:
            match = LEG_INPUT_PATTERN.fullmatch(value.strip())
            if match is None:
                raise typer.BadParameter(f'구간은 출발-도착-YYYY-MM-DD 로 주세요: {value}', param_hint='--leg')
            legs.append((match['origin'], match['destination'], parse_date(match['date'], '--leg')))
    else:
        if not (origin and destination and depart):
            raise typer.BadParameter('출발·도착과 --depart 를 주세요 (다구간은 --leg)', param_hint='ORIGIN DESTINATION --depart')
        legs = [(origin, destination, parse_date(depart, '--depart'))]
        if return_:
            legs.append((destination, origin, parse_date(return_, '--return')))
    dates = [date for _, _, date in legs]
    if dates != sorted(dates):
        raise typer.BadParameter('구간 날짜가 앞 구간보다 이릅니다', param_hint='--return / --leg')
    return legs


def validate_passengers(adults: int, children: int, infants: int) -> None:
    """승객 수 조합을 검사한다 (모두 9명까지, 유아는 성인 수 이하)."""
    if adults + children + infants > MAX_PASSENGERS:
        raise typer.BadParameter(f'승객은 모두 {MAX_PASSENGERS}명까지입니다', param_hint='--adults/--children/--infants')
    if infants > adults:
        raise typer.BadParameter('유아는 성인 수보다 많을 수 없습니다', param_hint='--infants')


def parse_airlines(values: list[str] | None) -> list[str]:
    """`--airline KE,OZ` / `--airline KE --airline OZ` 를 IATA 2자리 코드 목록으로."""
    airlines = [code.strip().upper() for value in values or [] for code in value.split(',') if code.strip()]
    for code in airlines:
        if AIRLINE_CODE_PATTERN.fullmatch(code) is None:
            raise typer.BadParameter(f'항공사는 IATA 2자리 코드로 주세요: {code}', param_hint='--airline')
    return list(dict.fromkeys(airlines))
