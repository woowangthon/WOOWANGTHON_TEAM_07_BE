from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import AppError
from app.main import create_app


def _assert_error_shape(body: dict) -> None:
    # 오류 응답은 error 객체 하나만 가지며, 그 안에는 code와 message만 있어야 한다.
    assert set(body.keys()) == {"error"}
    assert set(body["error"].keys()) == {"code", "message"}
    assert isinstance(body["error"]["code"], str) and body["error"]["code"]
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def test_unknown_path_returns_common_not_found() -> None:
    client = TestClient(create_app())

    response = client.get("/no-such-path")

    assert response.status_code == 404
    body = response.json()
    _assert_error_shape(body)
    assert body["error"]["code"] == "NOT_FOUND"


def test_method_not_allowed_keeps_allow_header() -> None:
    client = TestClient(create_app())

    response = client.post("/health")

    assert response.status_code == 405
    _assert_error_shape(response.json())
    assert "GET" in response.headers["allow"]


def test_app_error_is_rendered_with_its_status_and_code() -> None:
    app = create_app()

    @app.get("/_test/app-error")
    def raise_app_error() -> None:
        raise AppError(404, "STATION_NOT_FOUND", "역을 찾을 수 없습니다.")

    client = TestClient(app)

    response = client.get("/_test/app-error")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "STATION_NOT_FOUND", "message": "역을 찾을 수 없습니다."}
    }


def test_request_validation_error_returns_422_in_common_shape() -> None:
    app = create_app()

    @app.get("/_test/needs-int")
    def needs_int(n: int) -> dict:
        return {"n": n}

    client = TestClient(app)

    response = client.get("/_test/needs-int", params={"n": "abc"})

    assert response.status_code == 422
    body = response.json()
    _assert_error_shape(body)
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_unhandled_exception_returns_500_without_internal_details() -> None:
    app: FastAPI = create_app()

    @app.get("/_test/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/_test/boom")

    assert response.status_code == 500
    body = response.json()
    _assert_error_shape(body)
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "secret internal detail" not in response.text
