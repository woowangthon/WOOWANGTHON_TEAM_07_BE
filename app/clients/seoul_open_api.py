"""서울 열린데이터광장 OpenAPI 클라이언트 (httpx).

요청 형식: {base_url}/{KEY}/json/{서비스명}/{시작}/{끝}/   (한 번에 최대 1,000건)

보안 규칙
- API 키는 URL 경로에 들어간다. 로그, 예외 메시지, 객체 표현 어디에도 키를 남기지 않는다.
- 평문 HTTP를 기본으로 허용한다(팀 결정 2026-11-14). 문서화된 주소가 http://...:8088뿐이고
  HTTPS 연결은 개발 환경에서 TLS 핸드셰이크가 실패했다(2026-11-13, 서비스의 HTTPS 지원 여부는 단정하지 않음).
  키는 서버의 .env에만 두고 로그·예외에는 가린다. HTTPS만 허용하려면 allow_insecure_http=False를 준다.
- 리다이렉트를 따라가지 않는다(키가 다른 호스트로 새는 것을 막는다).
- 외부 응답의 오류 메시지에도 키가 들어 있을 수 있어 정리한 뒤에만 예외에 담는다.
- 예외는 `from None`으로 던져 원인 예외(URL을 담을 수 있음)가 따라가지 않게 한다.

동작 규칙
- 자동 재시도를 하지 않는다. 실패는 그대로 올린다(실패 처리는 적재 단계에서 한다).
- 연결 5초, 읽기 10초 타임아웃.
- 결과 코드: INFO-000 정상, INFO-200 빈 결과, 그 밖의 코드와 RESULT 누락은 오류.
- 첫 페이지의 list_total_count로 페이지 수를 정한다. 페이지 사이에 전체 건수가 바뀌거나
  페이지의 행 수가 기대와 다르면 오류다(일부만 쓰지 않는다).
"""

import logging
import math
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://openapi.seoul.go.kr:8088"
PAGE_SIZE = 1000
TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
MASK = "****"
MAX_MESSAGE_LENGTH = 200

CODE_OK = "INFO-000"
CODE_NO_DATA = "INFO-200"

# 키가 들어가는 URL 구간: http(s)://host/KEY/json/ 또는 /xml/
_URL_KEY_PATTERN = re.compile(r"(https?://[^/\s'\"]+/)([^/\s'\"]+)(/(?:json|xml)/)")


class SeoulOpenApiError(Exception):
    """클라이언트 오류의 공통 부모. 메시지에는 키가 들어가지 않는다."""


class SeoulOpenApiConfigError(SeoulOpenApiError):
    """키가 없거나 base_url이 허용되지 않을 때."""


class SeoulOpenApiTransportError(SeoulOpenApiError):
    """네트워크 오류, 타임아웃, 200이 아닌 HTTP 상태."""


class SeoulOpenApiResponseError(SeoulOpenApiError):
    """응답 본문이 규칙에 맞지 않거나 오류 코드를 돌려줄 때."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


def redact(text: str, api_key: str | None = None) -> str:
    """문자열에서 API 키를 가린다. URL의 키 구간과 키 값 자체(URL 인코딩 포함)를 모두 가린다."""
    result = _URL_KEY_PATTERN.sub(rf"\1{MASK}\3", text)
    if api_key:
        for variant in {api_key, quote(api_key, safe=""), quote(api_key)}:
            if variant:
                result = result.replace(variant, MASK)
    return result


_ANY_URL_PATTERN = re.compile(r"https?://[^\s'\"<>]+")
URL_PLACEHOLDER = "[URL 생략]"


def strip_urls(text: str) -> str:
    """문자열의 http(s) URL을 통째로 지운다. 키 구간만 가리는 redact와 달리 호스트와 경로도 남기지 않는다.

    사용자에게 보여 주거나 파일에 남길 외부 유래 문자열에 쓴다(요청 URL을 노출하지 않기 위해서다).
    """
    return _ANY_URL_PATTERN.sub(URL_PLACEHOLDER, text)


def mask_url(url: str, api_key: str | None = None) -> str:
    """로그에 쓸 URL. 키 구간을 ****로 바꾼다."""
    return redact(url, api_key)


class _RedactFilter(logging.Filter):
    """httpx/httpcore가 남기는 요청 URL 로그에서 키를 가린다."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def _install_log_redaction() -> None:
    for name in ("httpx", "httpcore"):
        target = logging.getLogger(name)
        if not any(isinstance(f, _RedactFilter) for f in target.filters):
            target.addFilter(_RedactFilter())


_install_log_redaction()


@dataclass(frozen=True)
class Page:
    code: str
    message: str
    total: int
    rows: list[dict]


@dataclass(frozen=True)
class FetchResult:
    rows: list[dict]
    total: int
    pages: int


def _clean_message(value: Any, api_key: str | None) -> str:
    text = redact(str(value), api_key).replace("\n", " ").strip()
    return text[:MAX_MESSAGE_LENGTH]


def _coerce_total(value: Any) -> int:
    if isinstance(value, bool):
        raise SeoulOpenApiResponseError("list_total_count 형식이 올바르지 않습니다.")
    if isinstance(value, int):
        total = value
    elif isinstance(value, str) and value.strip().isdigit():
        total = int(value.strip())
    else:
        raise SeoulOpenApiResponseError("list_total_count가 없거나 숫자가 아닙니다.")
    if total < 0:
        raise SeoulOpenApiResponseError("list_total_count가 음수입니다.")
    return total


def parse_envelope(payload: Any, service: str, api_key: str | None = None) -> Page:
    """원본 응답 한 페이지를 해석한다.

    정상: {service: {"list_total_count": n, "RESULT": {"CODE", "MESSAGE"}, "row": [...]}}
    오류: 최상위에 {"RESULT": {...}}만 있다(docs/api-notes.md).
    """
    if not isinstance(payload, dict):
        raise SeoulOpenApiResponseError("응답이 JSON 객체가 아닙니다.")

    body = payload.get(service)
    if body is None:
        result = payload.get("RESULT")
    elif isinstance(body, dict):
        result = body.get("RESULT")
    else:
        raise SeoulOpenApiResponseError("서비스 본문이 객체가 아닙니다.")
    if result is None:
        raise SeoulOpenApiResponseError("응답에 RESULT가 없습니다.")
    if not isinstance(result, dict) or not isinstance(result.get("CODE"), str):
        raise SeoulOpenApiResponseError("RESULT.CODE가 없습니다.")

    code = result["CODE"]
    message = _clean_message(result.get("MESSAGE", ""), api_key)
    if code == CODE_NO_DATA:
        return Page(code=code, message=message, total=0, rows=[])
    if code != CODE_OK:
        safe_code = redact(code, api_key)
        raise SeoulOpenApiResponseError(f"오류 응답 {safe_code}: {message}", code=safe_code)
    if not isinstance(body, dict):
        raise SeoulOpenApiResponseError("정상 코드인데 서비스 본문이 없습니다.")

    total = _coerce_total(body.get("list_total_count"))
    rows = body.get("row", [])
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise SeoulOpenApiResponseError("row가 객체 배열이 아닙니다.")
    return Page(code=code, message=message, total=total, rows=rows)


class SeoulOpenApiClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        allow_insecure_http: bool = True,
        transport: httpx.BaseTransport | None = None,
        timeout: httpx.Timeout = TIMEOUT,
    ) -> None:
        key = api_key.strip() if isinstance(api_key, str) else ""
        if not key:
            raise SeoulOpenApiConfigError("API 키가 비어 있습니다.")
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise SeoulOpenApiConfigError("base_url 형식이 올바르지 않습니다.")
        if parts.scheme == "http" and not allow_insecure_http:
            raise SeoulOpenApiConfigError(
                "평문 HTTP로 API 키를 보낼 수 없도록 설정되어 있습니다. HTTPS base_url을 쓰세요."
            )
        self._key = key
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout, transport=transport, follow_redirects=False)

    def __repr__(self) -> str:
        return f"SeoulOpenApiClient(base_url={self._base_url!r}, api_key={MASK})"

    def __enter__(self) -> "SeoulOpenApiClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _url(self, service: str, start: int, end: int, args: tuple[str, ...] = ()) -> str:
        extra = "".join(f"{quote(a, safe='')}/" for a in args)
        return f"{self._base_url}/{quote(self._key, safe='')}/json/{service}/{start}/{end}/{extra}"

    def fetch_page(self, service: str, start: int, end: int, args: tuple[str, ...] = ()) -> Page:
        """args는 시작·끝 뒤에 붙는 서비스별 요청 인자(예: 역 코드, 요일, 방향)다."""
        url = self._url(service, start, end, args)
        logger.info("OpenAPI 호출: %s", mask_url(url, self._key))

        # 예외는 except 블록 밖에서 던진다. 블록 안에서 던지면 원본 예외(요청 URL, 곧 키를 담을 수 있음)가
        # __context__로 남는다. `from None`은 표시만 숨기고 연결은 끊지 못한다.
        failure: SeoulOpenApiError | None = None
        response: httpx.Response | None = None
        try:
            response = self._client.get(url)
        except httpx.TimeoutException:
            failure = SeoulOpenApiTransportError(f"요청 시간 초과 (범위 {start}-{end})")
        except httpx.HTTPError as exc:
            failure = SeoulOpenApiTransportError(f"네트워크 오류 {type(exc).__name__} (범위 {start}-{end})")
        if failure is not None:
            raise failure
        assert response is not None

        if response.status_code != 200:
            raise SeoulOpenApiTransportError(f"HTTP {response.status_code} (범위 {start}-{end})")

        payload: Any = None
        try:
            payload = response.json()
        except ValueError:
            failure = SeoulOpenApiResponseError(f"JSON으로 해석할 수 없는 응답 (범위 {start}-{end})")
        if failure is not None:
            raise failure
        return parse_envelope(payload, service, self._key)

    def fetch_all_rows(self, service: str, args: tuple[str, ...] = ()) -> FetchResult:
        """전체 행을 페이지 단위로 받는다. 어느 단계든 어긋나면 예외를 올리고 부분 결과를 돌려주지 않는다."""
        first = self.fetch_page(service, 1, PAGE_SIZE, args)
        if first.code == CODE_NO_DATA or first.total == 0:
            return FetchResult(rows=[], total=0, pages=1)

        total = first.total
        pages = math.ceil(total / PAGE_SIZE)
        if len(first.rows) != min(PAGE_SIZE, total):
            raise SeoulOpenApiResponseError(
                f"첫 페이지 행 수({len(first.rows)})가 기대({min(PAGE_SIZE, total)})와 다릅니다."
            )
        rows = list(first.rows)
        for page_no in range(2, pages + 1):
            start = (page_no - 1) * PAGE_SIZE + 1
            end = min(page_no * PAGE_SIZE, total)
            page = self.fetch_page(service, start, end, args)
            if page.code == CODE_NO_DATA:
                raise SeoulOpenApiResponseError(f"범위 {start}-{end}에 데이터가 없습니다(INFO-200).")
            if page.total != total:
                raise SeoulOpenApiResponseError(
                    f"전체 건수가 페이지 사이에 바뀌었습니다: {total} -> {page.total}"
                )
            if len(page.rows) != end - start + 1:
                raise SeoulOpenApiResponseError(
                    f"범위 {start}-{end}의 행 수({len(page.rows)})가 기대({end - start + 1})와 다릅니다."
                )
            rows.extend(page.rows)
        if len(rows) != total:
            raise SeoulOpenApiResponseError(f"받은 행 수({len(rows)})가 전체 건수({total})와 다릅니다.")
        return FetchResult(rows=rows, total=total, pages=pages)
