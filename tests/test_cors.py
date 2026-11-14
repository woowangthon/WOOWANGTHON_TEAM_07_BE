"""CORS: 기본은 교차 출처를 허용하지 않고, 환경 변수에 명시한 Origin만 허용한다."""

import pytest
from fastapi.testclient import TestClient

from app import config
from app.cors import parse_origins
from app.main import create_app

ORIGIN = "http://localhost:5173"


def make_client(monkeypatch, value):
    monkeypatch.setattr(config.settings, "cors_allowed_origins", value)
    return TestClient(create_app())


def preflight(client, origin, method="POST"):
    return client.options("/api/departure-time", headers={
        "Origin": origin, "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": "content-type"})


def test_default_is_empty_and_adds_no_cors_headers(monkeypatch):
    assert config.Settings(_env_file=None).cors_allowed_origins == ""
    client = make_client(monkeypatch, "")

    response = client.get("/health", headers={"Origin": ORIGIN})
    assert response.status_code == 200 and "access-control-allow-origin" not in response.headers
    assert "access-control-allow-origin" not in preflight(client, ORIGIN).headers


def test_listed_origin_is_allowed(monkeypatch):
    client = make_client(monkeypatch, f"{ORIGIN}, https://demo.example.com/")

    response = client.get("/health", headers={"Origin": ORIGIN})
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-credentials" not in response.headers
    ok = preflight(client, "https://demo.example.com")
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "https://demo.example.com"
    assert "POST" in ok.headers["access-control-allow-methods"]


def test_unlisted_origin_and_method_are_rejected(monkeypatch):
    client = make_client(monkeypatch, ORIGIN)

    assert "access-control-allow-origin" not in client.get("/health", headers={"Origin": "http://evil.example"}).headers
    assert preflight(client, "http://evil.example").status_code == 400
    assert preflight(client, ORIGIN, method="DELETE").status_code == 400


@pytest.mark.parametrize("bad", ["*", "http://*.example.com", "localhost:5173", "ftp://a.com",
                                 "http://a.com/path", "http://a.com?x=1", "null"])
def test_invalid_origins_fail_at_startup(monkeypatch, bad):
    monkeypatch.setattr(config.settings, "cors_allowed_origins", bad)
    with pytest.raises(ValueError):
        create_app()


def test_parse_origins_normalizes_and_dedupes():
    assert parse_origins(" http://a.com/ ,http://a.com,, https://b.com:8443 ") == ["http://a.com", "https://b.com:8443"]
    assert parse_origins("") == []
