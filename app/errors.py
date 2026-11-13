"""공통 오류 응답.

모든 오류 응답은 아래 형식을 따른다.

    {"error": {"code": "NOT_FOUND", "message": "..."}}

- 서비스 코드는 AppError를 던지면 된다.
- FastAPI/Starlette의 HTTP 오류(404, 405 등)와 요청 검증 오류(422)도 같은 형식으로 바꾼다.
- 처리되지 않은 예외는 500 INTERNAL_ERROR로 바꾸고, 내부 정보(스택 트레이스 등)는 응답에 넣지 않는다.
"""

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.errors import ErrorBody, ErrorResponse

logger = logging.getLogger(__name__)

CODE_NOT_FOUND = "NOT_FOUND"
CODE_METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
CODE_VALIDATION_ERROR = "VALIDATION_ERROR"
CODE_HTTP_ERROR = "HTTP_ERROR"
CODE_INTERNAL_ERROR = "INTERNAL_ERROR"

_HTTP_CODES = {404: CODE_NOT_FOUND, 405: CODE_METHOD_NOT_ALLOWED}
_DEFAULT_MESSAGES = {
    404: "요청한 리소스를 찾을 수 없습니다.",
    405: "허용되지 않은 메서드입니다.",
}
_FALLBACK_MESSAGE = "요청을 처리할 수 없습니다."


class AppError(Exception):
    """서비스 계층에서 던지는 업무 오류. 상태 코드, 오류 코드, 메시지를 함께 지정한다."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _error_response(
    status_code: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message))
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(),
        headers=headers,
    )


async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return _error_response(exc.status_code, exc.code, exc.message)


async def _http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    status = exc.status_code
    detail = exc.detail
    # 라우트에서 직접 준 상세 메시지가 있으면 그대로 쓰고, 기본 영문 문구는 한국어로 바꾼다.
    if isinstance(detail, str) and detail != HTTPStatus(status).phrase:
        message = detail
    else:
        message = _DEFAULT_MESSAGES.get(status, _FALLBACK_MESSAGE)
    return _error_response(
        status,
        _HTTP_CODES.get(status, CODE_HTTP_ERROR),
        message,
        headers=exc.headers,
    )


async def _validation_error_handler(_: Request, __: RequestValidationError) -> JSONResponse:
    return _error_response(
        422,
        CODE_VALIDATION_ERROR,
        "요청 값이 올바르지 않습니다.",
    )


async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("처리되지 않은 오류", exc_info=exc)
    return _error_response(500, CODE_INTERNAL_ERROR, "서버 내부 오류가 발생했습니다.")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
