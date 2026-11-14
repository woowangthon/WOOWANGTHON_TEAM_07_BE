"""API 키 보호와 네트워크 격리 검증.

진짜 httpx/httpcore 전송 계층(실제 소켓)을 거치도록 127.0.0.1의 임시 서버를 쓴다.
서울 열린데이터광장 등 외부 호스트로는 연결하지 않으며, conftest의 가드가 이를 막는다.
키는 가짜 값이다.
"""

import json
import logging
import socket
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app.clients import seoul_open_api as api
from app.clients.seoul_open_api import (
    SeoulOpenApiClient,
    SeoulOpenApiConfigError,
    SeoulOpenApiError,
    SeoulOpenApiResponseError,
    SeoulOpenApiTransportError,
)

KEY = "FAKEKEY-abc123XYZ"
SERVICE = "SeoulMetroFaciInfo"
OK_BODY = {SERVICE: {"list_total_count": 2, "RESULT": {"CODE": "INFO-000", "MESSAGE": "ok"}, "row": [{"a": "1"}, {"a": "2"}]}}


class _Handler(BaseHTTPRequestHandler):
    """서비스 이름으로 동작을 고르는 임시 서버. 서비스명: Ok, Err500, Slow, LeakyError."""

    def do_GET(self):  # noqa: N802
        _key, _fmt, service, *_ = self.path.strip("/").split("/")
        if service == "Err500":
            self._send(500, {"error": "boom"})
        elif service == "Slow":
            time.sleep(1.0)
            self._send(200, OK_BODY)
        elif service == "LeakyError":
            # 서버가 오류 메시지에 요청 경로(키 포함)를 되돌려 주는 경우
            self._send(200, {"RESULT": {"CODE": "ERROR-290", "MESSAGE": f"invalid request {self.path}"}})
        else:
            self._send(200, OK_BODY)

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 서버 로그는 남기지 않는다
        pass


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def real_client(base_url, **kwargs) -> SeoulOpenApiClient:
    # transport를 직접 주면 httpx가 환경변수 프록시를 쓰지 않는다. 실제 소켓 전송 계층을 쓴다.
    return SeoulOpenApiClient(
        KEY, base_url=base_url, allow_insecure_http=True, transport=httpx.HTTPTransport(), **kwargs
    )


def everything_about(exc: BaseException) -> str:
    """예외에서 사람이나 도구가 볼 수 있는 모든 표현을 합친다."""
    parts = [str(exc), repr(exc), repr(exc.args), "".join(traceback.format_exception(exc))]
    seen = set()
    stack = [exc]
    while stack:  # 표시 여부와 무관하게 __cause__/__context__ 연결을 끝까지 따라간다
        e = stack.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        parts += [str(e), repr(e), repr(getattr(e, "args", ""))]
        stack += [e.__cause__, e.__context__]
    return "\n".join(parts)


# ---------- 1. httpx / httpcore DEBUG·INFO 로그 ----------


def test_httpx_and_httpcore_debug_logs_never_contain_the_key(server, caplog, loggers_enabled):
    caplog.set_level(logging.DEBUG)
    with real_client(server) as client:
        client.fetch_all_rows(SERVICE)

    names = {r.name for r in caplog.records}
    assert "httpx" in names  # 실제로 httpx가 로그를 남겼고
    assert any(n.startswith("httpcore") for n in names)  # httpcore DEBUG 로그도 남았다
    for record in caplog.records:
        assert KEY not in record.getMessage()
        assert KEY not in repr(record.args) and KEY not in str(record.msg)
        assert KEY not in (record.exc_text or "")
    assert KEY not in caplog.text


def test_httpx_info_log_shows_the_masked_url(server, caplog, loggers_enabled):
    caplog.set_level(logging.INFO)
    with real_client(server) as client:
        client.fetch_all_rows(SERVICE)

    request_logs = [r.getMessage() for r in caplog.records if r.name == "httpx"]
    assert request_logs and all("/****/json/" in m for m in request_logs)


def test_failed_requests_do_not_log_the_key_either(server, caplog, loggers_enabled):
    caplog.set_level(logging.DEBUG)
    with real_client(server) as client, pytest.raises(SeoulOpenApiTransportError):
        client.fetch_all_rows("Err500")

    assert KEY not in caplog.text


# ---------- 2. 예외 (str, repr, args, traceback, 체인) ----------


def test_http_error_exception_exposes_no_key(server):
    with real_client(server) as client, pytest.raises(SeoulOpenApiTransportError) as info:
        client.fetch_all_rows("Err500")

    assert KEY not in everything_about(info.value)
    assert info.value.__cause__ is None and info.value.__context__ is None


def test_connection_refused_exposes_no_key():
    with socket.socket() as s:  # 아무도 듣지 않는 루프백 포트를 얻는다
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with real_client(f"http://127.0.0.1:{port}") as client, pytest.raises(SeoulOpenApiTransportError) as info:
        client.fetch_all_rows(SERVICE)

    assert KEY not in everything_about(info.value)
    assert info.value.__cause__ is None and info.value.__context__ is None


def test_read_timeout_exposes_no_key(server):
    with real_client(server, timeout=httpx.Timeout(0.2)) as client, pytest.raises(SeoulOpenApiTransportError, match="시간 초과") as info:
        client.fetch_all_rows("Slow")

    assert KEY not in everything_about(info.value)
    assert info.value.__cause__ is None and info.value.__context__ is None


def test_server_error_message_echoing_the_key_is_sanitized(server):
    with real_client(server) as client, pytest.raises(SeoulOpenApiResponseError) as info:
        client.fetch_all_rows("LeakyError")

    text = everything_about(info.value)
    assert KEY not in text
    assert "ERROR-290" in text and "****" in text


def test_exceptions_from_mock_transport_have_no_linked_originals():
    def handler(request):
        raise httpx.ConnectError(f"cannot connect to {request.url}")

    client = SeoulOpenApiClient(KEY, base_url="https://mock.test", transport=httpx.MockTransport(handler))
    with pytest.raises(SeoulOpenApiError) as info:
        client.fetch_all_rows(SERVICE)

    assert info.value.__context__ is None and info.value.__cause__ is None
    assert KEY not in everything_about(info.value)


def test_non_json_response_exposes_no_key():
    client = SeoulOpenApiClient(
        KEY, base_url="https://mock.test",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=f"not json {r.url}")),
    )
    with pytest.raises(SeoulOpenApiResponseError) as info:
        client.fetch_all_rows(SERVICE)

    assert info.value.__context__ is None
    assert KEY not in everything_about(info.value)


# ---------- 3. 평문 HTTP 차단 옵션과 네트워크 격리 ----------


def test_plain_http_is_rejected_before_any_connection_when_blocked(block_external_network, monkeypatch):
    attempted = []
    real = socket.socket.connect
    monkeypatch.setattr(socket.socket, "connect", lambda self, addr, *a, **k: (attempted.append(addr), real(self, addr, *a, **k))[1])

    with pytest.raises(SeoulOpenApiConfigError, match="평문 HTTP"):
        SeoulOpenApiClient(KEY, allow_insecure_http=False)  # 기본 base_url
    with pytest.raises(SeoulOpenApiConfigError, match="평문 HTTP"):
        SeoulOpenApiClient(KEY, base_url="http://openapi.seoul.go.kr:8088", allow_insecure_http=False)

    assert attempted == [] and block_external_network == []


def test_network_guard_blocks_external_hosts(block_external_network):
    with pytest.raises(OSError, match="외부 네트워크"):
        socket.create_connection(("openapi.seoul.go.kr", 8088), timeout=1)
    with pytest.raises(OSError, match="외부 네트워크"):
        socket.getaddrinfo("example.com", 80)

    assert block_external_network == ["openapi.seoul.go.kr", "example.com"]
    block_external_network.clear()  # 일부러 일으킨 시도이므로 확인한 뒤 비운다


def test_guard_blocks_external_requests_through_the_client(block_external_network):
    client = SeoulOpenApiClient(KEY, base_url="https://openapi.seoul.go.kr:8088", transport=httpx.HTTPTransport())
    with client, pytest.raises(SeoulOpenApiTransportError) as info:
        client.fetch_all_rows(SERVICE)  # 실제로는 나가지 않고 가드가 막는다

    assert block_external_network == ["openapi.seoul.go.kr"]
    assert KEY not in everything_about(info.value)
    block_external_network.clear()  # 일부러 일으킨 시도이므로 확인한 뒤 비운다


def test_mock_transport_tests_make_no_connections(block_external_network):
    handler = lambda request: httpx.Response(200, json=OK_BODY)  # noqa: E731
    with SeoulOpenApiClient(KEY, base_url="https://mock.test", transport=httpx.MockTransport(handler)) as client:
        assert client.fetch_all_rows(SERVICE).total == 2

    assert block_external_network == []


# ---------- 4. 로그 마스킹이 다른 로거를 방해하지 않는다 ----------


def test_filter_is_attached_only_to_httpx_and_httpcore():
    holders = [
        name for name in ["", "app", "app.clients.seoul_open_api", "sqlalchemy", "alembic", "uvicorn", "fastapi", "starlette"]
        if any(isinstance(f, api._RedactFilter) for f in logging.getLogger(name).filters)
    ]

    assert holders == []
    for name in ("httpx", "httpcore"):
        assert any(isinstance(f, api._RedactFilter) for f in logging.getLogger(name).filters)
    assert not any(isinstance(f, api._RedactFilter) for h in logging.getLogger().handlers for f in h.filters)


def test_other_loggers_keep_their_messages_even_if_they_look_like_urls(caplog):
    caplog.set_level(logging.INFO)
    url = "http://openapi.seoul.go.kr:8088/visible-segment/json/Svc/1/5/"
    logging.getLogger("app.other").info("다른 로거: %s", url)

    assert url in caplog.text  # 마스킹은 다른 로거의 내용을 바꾸지 않는다


def test_httpx_messages_without_a_key_pass_through_unchanged(caplog, loggers_enabled):
    caplog.set_level(logging.INFO)
    logging.getLogger("httpx").info("plain %s and %d", "text", 3)

    record = [r for r in caplog.records if r.name == "httpx"][-1]
    assert record.getMessage() == "plain text and 3"
    assert record.args == ("text", 3)  # 키가 없으면 레코드를 건드리지 않는다


def test_filter_never_drops_records():
    record = logging.LogRecord("httpx", logging.INFO, __file__, 1, f"GET http://h.test/{KEY}/json/S/1/2/", (), None)

    assert api._RedactFilter().filter(record) is True
    assert KEY not in record.getMessage()


def test_filter_keeps_exception_info_attached():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        exc_info = sys.exc_info()
    record = logging.LogRecord("httpx", logging.ERROR, __file__, 1, "plain message", (), exc_info)

    api._RedactFilter().filter(record)

    assert record.exc_info == exc_info
    assert record.getMessage() == "plain message"


def test_logging_levels_and_handlers_are_not_changed_by_importing_the_client():
    assert logging.getLogger("httpx").level in (logging.NOTSET, logging.WARNING, logging.INFO, logging.DEBUG)
    assert logging.getLogger("app.clients.seoul_open_api").handlers == []  # 핸들러를 추가하지 않는다
    assert logging.getLogger("httpx").handlers == []
