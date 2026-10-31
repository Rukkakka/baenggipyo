from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
)

from baenggipyo.core.errors import BaenggipyoError


class RequestModel(BaseModel):
    """Base request model with strict field validation."""
    model_config = ConfigDict(
        validate_by_name=True,
        extra='forbid',
    )


class ResponseModel(BaseModel):
    """Base response model allowing additional fields from APIs.

    비공식 API 는 예고 없이 필드를 추가하므로 모르는 필드는 버리지 않고 보존한다.
    """
    model_config = ConfigDict(
        validate_by_name=True,
        extra='allow',
    )


def is_request_validation_error(exc: ValidationError) -> bool:
    """요청 모델(`RequestModel`)을 만들다 난 검증 오류인지. 즉 사용자 입력이 잘못된 경우.

    응답 모델이나 공통 출력 모델 검증 오류는 소스 응답이 예상과 달라진 것이라 입력 오류가 아니다.
    `ValidationError.title` 은 검증한 모델 클래스 이름이다. 모델 클래스로만 가르므로, 앞 응답의 값으로 만드는 요청 모델은
    `built_from_response()` 안에서 만들어 입력 오류로 잘못 가르지 않게 한다. 기본값을 입력 오류로 둔 이유(사용자 값이 들어가는 길이 더 많다)는
    docs/development/provider-conventions.md '요청·응답 모델' 에 있다.
    """
    stack: list[type[BaseModel]] = [RequestModel]
    while stack:
        model = stack.pop()
        if model.__name__ == exc.title:
            return True
        stack.extend(model.__subclasses__())
    return False


@contextmanager
def built_from_response(what: str) -> Iterator[None]:
    """앞 응답의 값을 옮겨 담아 요청 모델을 만드는 구간 (판매처 조회의 운임·여정 날짜 등).

    여기서 난 요청 모델 검증 오류는 입력을 고칠 일이 아니라 응답이 예상과 다른 것이라 `INVALID_ARGUMENT` 대신
    `PARSE_ERROR` 로 낸다. 사용자 입력은 이 구간에 들어오기 전에 CLI·조건 모델에서 이미 검사했다.

    Args:
        what: 만들던 요청 (예: '네이버 판매처 조회').

    Raises:
        BaenggipyoError: 구간 안에서 검증이 실패했을 때 (`PARSE_ERROR`).
    """
    try:
        yield
    except ValidationError as exc:
        raise BaenggipyoError(
            code='PARSE_ERROR',
            message=f'{what} 요청을 응답 값으로 만들지 못했습니다 ({exc.title}): ' + '; '.join(error['msg'] for error in exc.errors()),
            hint='비공식 API 응답 형식이 바뀌었을 수 있습니다. 다른 소스를 쓰세요',
        ) from exc
