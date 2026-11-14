"""서울 열린데이터광장 클라이언트 검증. MockTransport만 쓰며 실제 네트워크 호출은 없다.

키는 가짜 값이다. 실제 키(.env)는 읽지 않는다.
"""

import json
import logging
from pathlib import Path

import httpx
import pytest

from app.clients import seoul_open_api as api
from app.clients.oa15994 import SERVICE_NAME, load_sample_rows
from app.clients.seoul_open_api import (
    SeoulOpenApiClient,
    SeoulOpenApiConfigError,
    SeoulOpenApiResponseError,
    SeoulOpenApiTransportError,
    mask_url,
    parse_envelope,
    redact,
)

KEY = "FAKEKEY-abc123XYZ"
BASE = "https://mock.test"
SAMPLE = Path(__file__).resolve().parent.parent / "docs" / "samples" / "OA-15994_SeoulMetroFaciInfo_2026-11-13.json"
OK = {"CODE": "INFO-000", "MESSAGE": "정상 처리되었습니다"}


@pytest.fixture(scope="module")
def sample_rows() -> list[dict]:
    return load_sample_rows(json.loads(SAMPLE.read_text(encoding="utf-8")))


def envelope(rows, total=None, result=OK):
    body = {"list_total_count": len(rows) if total is None else total, "row": rows}
    if result is not None:
        body["RESULT"] = result
    return {SERVICE_NAME: body}


class FakeServer:
    """요청 경로(/KEY/json/서비스/시작/끝/)를 읽어 행을 잘라 돌려주는 가짜 서버."""

    def __init__(self, rows, total=None, override=None):
        self.rows = rows
        self.total = len(rows) if total is None else total
        self.override = override or {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        number = len(self.requests)
        if number in self.override:
            return self.override[number](request)
        _, _, service, start, end = request.url.path.strip("/").split("/")
        chunk = self.rows[int(start) - 1 : int(end)]
        return httpx.Response(200, json=envelope(chunk, self.total))

    @property
    def ranges(self) -> list[str]:
        return ["/".join(r.url.path.strip("/").split("/")[3:]) for r in self.requests]


def make_client(handler, **kwargs) -> SeoulOpenApiClient:
    return SeoulOpenApiClient(KEY, base_url=BASE, transport=httpx.MockTransport(handler), **kwargs)


def respond(payload=None, status=200, text=None):
    def handler(_request):
        if text is not None:
            return httpx.Response(status, text=text)
        return httpx.Response(status, json=payload)
    return handler


def rows_of(n: int) -> list[dict]:
    return [{"STN_CD": f"{i:04d}", "ELVTR_NM": f"e{i}"} for i in range(n)]


# ---------- 페이지네이션 ----------


def test_sample_is_fetched_in_three_pages(sample_rows):
    server = FakeServer(sample_rows)
    with make_client(server) as client:
        result = client.fetch_all_rows(SERVICE_NAME)

    assert server.ranges == ["1/1000", "1001/2000", "2001/2809"]
    assert result.pages == 3 and result.total == 2809
    assert result.rows == sample_rows  # 순서까지 같다


@pytest.mark.parametrize(
    "count, expected",
    [(1, ["1/1000"]), (999, ["1/1000"]), (1000, ["1/1000"]), (1001, ["1/1000", "1001/1001"]),
     (2000, ["1/1000", "1001/2000"]), (2001, ["1/1000", "1001/2000", "2001/2001"])],
)
def test_page_count_follows_total_count(count, expected):
    server = FakeServer(rows_of(count))
    with make_client(server) as client:
        result = client.fetch_all_rows(SERVICE_NAME)

    assert server.ranges == expected
    assert len(result.rows) == count


def test_request_url_has_documented_shape():
    server = FakeServer(rows_of(3))
    with make_client(server) as client:
        client.fetch_all_rows(SERVICE_NAME)

    request = server.requests[0]
    assert request.url.host == "mock.test"
    assert request.url.path == f"/{KEY}/json/{SERVICE_NAME}/1/1000/"
    assert request.method == "GET"


# ---------- 결과 코드 ----------


def test_info_200_on_first_page_is_an_empty_result():
    handler = respond({"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}})
    with make_client(handler) as client:
        result = client.fetch_all_rows(SERVICE_NAME)

    assert result.rows == [] and result.total == 0


def test_info_200_inside_service_body_is_an_empty_result():
    payload = {SERVICE_NAME: {"RESULT": {"CODE": "INFO-200", "MESSAGE": "없음"}}}
    with make_client(respond(payload)) as client:
        assert client.fetch_all_rows(SERVICE_NAME).rows == []


def test_info_200_on_a_later_page_is_an_error():
    rows = rows_of(1500)
    server = FakeServer(rows, override={2: respond({"RESULT": {"CODE": "INFO-200", "MESSAGE": "없음"}})})
    with make_client(server) as client, pytest.raises(SeoulOpenApiResponseError, match="INFO-200"):
        client.fetch_all_rows(SERVICE_NAME)


@pytest.mark.parametrize("code", ["ERROR-300", "ERROR-336", "ERROR-500", "INFO-100", "INFO-001"])
def test_other_result_codes_are_errors(code):
    handler = respond({"RESULT": {"CODE": code, "MESSAGE": "문제가 있습니다"}})
    with make_client(handler) as client, pytest.raises(SeoulOpenApiResponseError) as info:
        client.fetch_all_rows(SERVICE_NAME)

    assert code in str(info.value)
    assert info.value.code == code


def test_error_code_inside_service_body_is_an_error():
    payload = envelope([], result={"CODE": "ERROR-500", "MESSAGE": "서버 오류"})
    with make_client(respond(payload)) as client, pytest.raises(SeoulOpenApiResponseError, match="ERROR-500"):
        client.fetch_all_rows(SERVICE_NAME)


# ---------- 응답 형식 오류 ----------


def test_missing_result_is_an_error():
    with make_client(respond(envelope(rows_of(2), result=None))) as client, pytest.raises(
        SeoulOpenApiResponseError, match="RESULT"
    ):
        client.fetch_all_rows(SERVICE_NAME)


@pytest.mark.parametrize(
    "payload",
    [{}, [], "text", 3, None, {"other": {}}, {"RESULT": "bad"}, {"RESULT": {"MESSAGE": "no code"}}, {"RESULT": {"CODE": 5}}],
)
def test_malformed_payloads_are_errors(payload):
    with make_client(respond(payload)) as client, pytest.raises(SeoulOpenApiResponseError):
        client.fetch_all_rows(SERVICE_NAME)


def test_non_json_body_is_an_error():
    with make_client(respond(text="<html>not json</html>")) as client, pytest.raises(
        SeoulOpenApiResponseError, match="JSON"
    ):
        client.fetch_all_rows(SERVICE_NAME)


def test_ok_code_without_service_body_is_an_error():
    with make_client(respond({"RESULT": OK})) as client, pytest.raises(SeoulOpenApiResponseError, match="본문"):
        client.fetch_all_rows(SERVICE_NAME)


@pytest.mark.parametrize("bad_total", ["abc", "MISSING", -1, True, 1.5, "-3", None])
def test_invalid_total_count_is_an_error(bad_total):
    payload = envelope([], total=0)
    if bad_total == "MISSING":
        del payload[SERVICE_NAME]["list_total_count"]  # 키 자체가 없는 경우
    else:
        payload[SERVICE_NAME]["list_total_count"] = bad_total  # None(null) 포함
    with make_client(respond(payload)) as client, pytest.raises(SeoulOpenApiResponseError, match="list_total_count"):
        client.fetch_all_rows(SERVICE_NAME)


def test_numeric_string_total_count_is_accepted():
    page = parse_envelope(envelope(rows_of(2), total="2"), SERVICE_NAME)

    assert page.total == 2


@pytest.mark.parametrize("rows", ["x", [1, 2], [{"a": 1}, "b"], {"a": 1}])
def test_rows_must_be_a_list_of_objects(rows):
    payload = {SERVICE_NAME: {"list_total_count": 1, "RESULT": OK, "row": rows}}
    with pytest.raises(SeoulOpenApiResponseError, match="row"):
        parse_envelope(payload, SERVICE_NAME)


# ---------- 페이지 간 일관성 ----------


def test_total_count_change_between_pages_is_an_error():
    rows = rows_of(1500)

    def second_page(request):
        _, _, _, start, end = request.url.path.strip("/").split("/")
        return httpx.Response(200, json=envelope(rows[int(start) - 1 : int(end)], total=1600))

    server = FakeServer(rows, override={2: second_page})
    with make_client(server) as client, pytest.raises(SeoulOpenApiResponseError, match="전체 건수가 페이지 사이에 바뀌었습니다"):
        client.fetch_all_rows(SERVICE_NAME)


def test_short_page_is_an_error():
    rows = rows_of(1500)
    server = FakeServer(rows, override={2: respond(envelope(rows[1000:1400], total=1500))})
    with make_client(server) as client, pytest.raises(SeoulOpenApiResponseError, match="행 수"):
        client.fetch_all_rows(SERVICE_NAME)


def test_short_first_page_is_an_error():
    with make_client(respond(envelope(rows_of(3), total=10))) as client, pytest.raises(
        SeoulOpenApiResponseError, match="첫 페이지"
    ):
        client.fetch_all_rows(SERVICE_NAME)


def test_failure_on_a_later_page_returns_no_partial_rows():
    server = FakeServer(rows_of(2500), override={3: respond({}, status=500)})
    client = make_client(server)
    with pytest.raises(SeoulOpenApiTransportError):
        client.fetch_all_rows(SERVICE_NAME)  # 예외로 끝나며 일부 행을 돌려주지 않는다
    assert len(server.requests) == 3


# ---------- HTTP·네트워크 오류, 타임아웃, 재시도 없음 ----------


@pytest.mark.parametrize("status", [301, 302, 400, 403, 404, 429, 500, 502, 503])
def test_non_200_status_is_a_transport_error(status):
    with make_client(respond({}, status=status)) as client, pytest.raises(SeoulOpenApiTransportError, match=f"HTTP {status}"):
        client.fetch_all_rows(SERVICE_NAME)


def test_redirects_are_not_followed():
    calls = []

    def handler(request):
        calls.append(str(request.url.host))
        return httpx.Response(302, headers={"Location": "https://evil.test/steal"})

    with make_client(handler) as client, pytest.raises(SeoulOpenApiTransportError, match="HTTP 302"):
        client.fetch_all_rows(SERVICE_NAME)

    assert calls == ["mock.test"]  # 다른 호스트로 요청이 가지 않았다


@pytest.mark.parametrize(
    "exc",
    [httpx.ConnectTimeout(f"timeout for {KEY}"), httpx.ReadTimeout(f"read timeout {KEY}"), httpx.PoolTimeout("pool")],
)
def test_timeouts_become_transport_errors_without_the_key(exc):
    def handler(_request):
        raise exc

    with make_client(handler) as client, pytest.raises(SeoulOpenApiTransportError, match="시간 초과") as info:
        client.fetch_all_rows(SERVICE_NAME)

    assert KEY not in str(info.value)
    assert info.value.__cause__ is None and info.value.__context__ is None  # 원본 예외가 연결되지 않는다


def test_network_errors_become_transport_errors_without_the_key():
    def handler(request):
        raise httpx.ConnectError(f"cannot connect to {request.url}")  # 원인 메시지에 URL(키 포함)이 있다

    with make_client(handler) as client, pytest.raises(SeoulOpenApiTransportError, match="ConnectError") as info:
        client.fetch_all_rows(SERVICE_NAME)

    assert KEY not in str(info.value)
    assert KEY not in repr(info.value)
    assert info.value.__cause__ is None and info.value.__context__ is None


def test_there_is_no_automatic_retry():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500, json={})

    with make_client(handler) as client, pytest.raises(SeoulOpenApiTransportError):
        client.fetch_all_rows(SERVICE_NAME)

    assert len(calls) == 1


def test_timeouts_are_5_seconds_to_connect_and_10_to_read():
    with make_client(FakeServer([])) as client:
        timeout = client._client.timeout

    assert timeout.connect == 5.0
    assert timeout.read == 10.0
    assert api.TIMEOUT.connect == 5.0 and api.TIMEOUT.read == 10.0


# ---------- 설정 검증 ----------


def test_plain_http_is_allowed_by_default():
    SeoulOpenApiClient(KEY).close()  # 기본 base_url은 http지만 기본 허용이다(팀 결정 2026-11-14)
    SeoulOpenApiClient(KEY, base_url="http://example.test").close()
    assert api.DEFAULT_BASE_URL.startswith("http://")


def test_plain_http_can_be_blocked_explicitly():
    with pytest.raises(SeoulOpenApiConfigError, match="평문 HTTP"):
        SeoulOpenApiClient(KEY, allow_insecure_http=False)
    with pytest.raises(SeoulOpenApiConfigError, match="평문 HTTP"):
        SeoulOpenApiClient(KEY, base_url="http://example.test", allow_insecure_http=False)


def test_plain_http_request_is_sent_over_http():
    server = FakeServer(rows_of(1))
    client = SeoulOpenApiClient(
        KEY, base_url="http://mock.test:8088", transport=httpx.MockTransport(server)
    )
    with client:
        client.fetch_all_rows(SERVICE_NAME)

    assert server.requests[0].url.scheme == "http"


def test_https_is_accepted_without_opt_in():
    SeoulOpenApiClient(KEY, base_url="https://example.test", transport=httpx.MockTransport(FakeServer([]))).close()


@pytest.mark.parametrize("base_url", ["", "ftp://x.test", "mock.test", "https://", "//mock.test"])
def test_invalid_base_url_is_rejected(base_url):
    with pytest.raises(SeoulOpenApiConfigError, match="base_url"):
        SeoulOpenApiClient(KEY, base_url=base_url)


@pytest.mark.parametrize("key", ["", "   ", None, 123])
def test_empty_key_is_rejected(key):
    with pytest.raises(SeoulOpenApiConfigError, match="API 키"):
        SeoulOpenApiClient(key, base_url=BASE)


def test_repr_hides_the_key():
    client = SeoulOpenApiClient(KEY, base_url=BASE, transport=httpx.MockTransport(FakeServer([])))

    assert KEY not in repr(client) and KEY not in str(client)
    assert "****" in repr(client)
    client.close()


# ---------- API 키 마스킹 ----------


def test_mask_url_hides_the_key_segment():
    url = f"http://openapi.seoul.go.kr:8088/{KEY}/json/SeoulMetroFaciInfo/1/1000/"

    masked = mask_url(url)

    assert KEY not in masked
    assert masked == "http://openapi.seoul.go.kr:8088/****/json/SeoulMetroFaciInfo/1/1000/"


def test_mask_url_works_for_xml_and_https():
    assert mask_url(f"https://h.test/{KEY}/xml/Svc/1/5/") == "https://h.test/****/xml/Svc/1/5/"


def test_redact_hides_key_inside_free_text():
    text = f"failed for {KEY} while calling http://h.test:8088/{KEY}/json/Svc/1/5/ twice {KEY}"

    cleaned = redact(text, KEY)

    assert KEY not in cleaned
    assert cleaned.count("****") == 3


def test_redact_hides_url_encoded_key():
    key = "a/b+c d"
    text = "path=/a%2Fb%2Bc%20d/json and /a/b%2Bc%20d/x"

    assert "a%2Fb%2Bc%20d" not in redact(text, key)


def test_redact_leaves_unrelated_text_alone():
    assert redact("그냥 문장 http://h.test/not-a-key-path", KEY) == "그냥 문장 http://h.test/not-a-key-path"
    assert redact("abc", None) == "abc"
    assert redact("abc", "") == "abc"


def test_key_with_special_characters_is_quoted_in_the_url():
    key = "a/b c"
    seen = []

    def handler(request):
        seen.append(request.url.raw_path)  # 실제로 전송되는 경로(디코딩 전)
        return httpx.Response(200, json=envelope(rows_of(1)))

    client = SeoulOpenApiClient(key, base_url=BASE, transport=httpx.MockTransport(handler))
    with client:
        client.fetch_all_rows(SERVICE_NAME)

    assert seen[0].startswith(b"/a%2Fb%20c/json/")  # 키의 /가 경로를 깨지 않는다


def test_external_error_message_with_key_is_sanitized():
    leaking = f"invalid key {KEY} at http://openapi.seoul.go.kr:8088/{KEY}/json/Svc/1/5/ " + "x" * 500
    handler = respond({"RESULT": {"CODE": "ERROR-290", "MESSAGE": leaking}})
    with make_client(handler) as client, pytest.raises(SeoulOpenApiResponseError) as info:
        client.fetch_all_rows(SERVICE_NAME)

    message = str(info.value)
    assert KEY not in message and KEY not in repr(info.value)
    assert "ERROR-290" in message
    assert len(message) < 320  # 외부 메시지는 길이도 제한한다


def test_external_info_200_message_with_key_is_sanitized():
    page = parse_envelope({"RESULT": {"CODE": "INFO-200", "MESSAGE": f"nothing for {KEY}"}}, SERVICE_NAME, KEY)

    assert KEY not in page.message


def test_no_exception_carries_the_key_across_failure_modes(sample_rows):
    """여러 실패 방식에서 예외의 문자열, repr, 인자, 연쇄 어디에도 키가 없다."""
    cases = [
        respond({}, status=500),
        respond(text="not json"),
        respond({"RESULT": {"CODE": "ERROR-1", "MESSAGE": f"{KEY}"}}),
        respond({"RESULT": {"CODE": f"ERR-{KEY}", "MESSAGE": "m"}}),
        lambda r: (_ for _ in ()).throw(httpx.ConnectError(str(r.url))),
    ]
    for handler in cases:
        with make_client(handler) as client, pytest.raises(api.SeoulOpenApiError) as info:
            client.fetch_all_rows(SERVICE_NAME)
        exc = info.value
        assert KEY not in str(exc) and KEY not in repr(exc) and KEY not in repr(exc.args)
        assert KEY not in str(getattr(exc, "code", "") or "")
        assert exc.__cause__ is None and exc.__context__ is None  # 원본 예외(키 포함 URL)가 어디에도 연결되지 않는다


def test_our_logs_never_contain_the_key(caplog, loggers_enabled):
    caplog.set_level(logging.DEBUG)
    server = FakeServer(rows_of(3))
    with make_client(server) as client:
        client.fetch_all_rows(SERVICE_NAME)

    assert KEY not in caplog.text
    assert any("****" in r.getMessage() for r in caplog.records if r.name == api.logger.name)


def test_httpx_request_logs_are_redacted(caplog, loggers_enabled):
    caplog.set_level(logging.DEBUG)
    # httpx는 요청 URL을 INFO로 남긴다. 필터가 키를 가려야 한다.
    logging.getLogger("httpx").info('HTTP Request: GET http://openapi.seoul.go.kr:8088/%s/json/Svc/1/5/ "HTTP/1.1 200 OK"', KEY)

    assert KEY not in caplog.text
    assert "http://openapi.seoul.go.kr:8088/****/json/Svc/1/5/" in caplog.text


def test_real_httpx_logging_during_a_fetch_is_redacted(caplog, loggers_enabled):
    caplog.set_level(logging.DEBUG)
    server = FakeServer(rows_of(3))
    with make_client(server) as client:
        client.fetch_all_rows(SERVICE_NAME)

    assert any(r.name == "httpx" for r in caplog.records)  # httpx가 실제로 로그를 남겼고
    assert KEY not in caplog.text  # 그 안에도 키가 없다


def test_log_filter_is_installed_only_once():
    api._install_log_redaction()
    api._install_log_redaction()  # 여러 번 불러도 필터가 중복되지 않는다
    filters = [f for f in logging.getLogger("httpx").filters if isinstance(f, api._RedactFilter)]

    assert len(filters) == 1
