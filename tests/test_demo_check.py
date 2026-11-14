"""시연 점검 스크립트 검증. 데모 DB를 임시 디렉터리에 만들고 TestClient로 점검한다(실서버·외부 호출 없음)."""

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import get_session, make_engine
from app.main import create_app
from app.services.demo_db import ROOT, build_demo_db

spec = importlib.util.spec_from_file_location("demo_check", ROOT / "scripts" / "demo_check.py")
demo_check = importlib.util.module_from_spec(spec)
sys.modules["demo_check"] = demo_check
spec.loader.exec_module(demo_check)


@pytest.fixture(scope="module")
def demo_client(tmp_path_factory):
    result = build_demo_db(tmp_path_factory.mktemp("check"), include_provisional_adjacent=True)
    engine = make_engine(f"sqlite:///{result.db_path}")
    session = Session(engine)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    session.close()
    engine.dispose()


def test_demo_db_passes_with_warnings_not_failures(demo_client, capsys):
    assert demo_check.main([], client=demo_client) == 0
    out = capsys.readouterr().out
    assert "FAIL=0" in out and "[WARN]" in out  # 정보 없음·후보 없음·UNCONFIRMED는 경고일 뿐이다


def test_empty_db_is_not_a_failure(client):  # 역이 없는 DB(conftest의 db는 역 10개, 검색어를 바꿔 결과 없음 상태로 만든다)
    report = demo_check.run_checks(client, query="없는역이름")

    assert not report.failed
    assert any(s == demo_check.SKIP for s, _, _ in report.results)
    assert any(s == demo_check.WARN and "검색 결과 없음" in d for s, _, d in report.results)


def test_server_down_fails_and_stops(capsys):
    def refuse(request):
        raise httpx.ConnectError("refused")

    client = httpx.Client(base_url="http://127.0.0.1:1", transport=httpx.MockTransport(refuse))
    report = demo_check.run_checks(client)

    assert report.failed and report.results[-1][0] == demo_check.SKIP


def test_contract_violations_fail():
    def bad(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(500, json={"error": {"code": "INTERNAL_ERROR", "message": "x"}})

    report = demo_check.run_checks(httpx.Client(base_url="http://x", transport=httpx.MockTransport(bad)))

    assert report.failed


def test_external_url_requires_flag(capsys):
    assert demo_check.main(["--base-url", "https://example.com"]) == 2
    assert "--allow-external" in capsys.readouterr().err
    assert demo_check.main(["--base-url", "ftp://x"]) == 2


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "http://localhost:9", "http://[::1]:8000"])
def test_local_urls_are_allowed(url):
    assert demo_check._is_local(url)


@pytest.mark.parametrize("url", ["https://example.com", "http://127.0.0.1.evil.com", "http://10.0.0.5"])
def test_non_local_urls_are_rejected(url):
    assert not demo_check._is_local(url)


def test_output_has_no_env_values(demo_client, capsys, monkeypatch):
    monkeypatch.setenv("SEOUL_OPEN_API_KEY", "SECRET-VALUE-123")
    demo_check.main([], client=demo_client)

    assert "SECRET-VALUE-123" not in capsys.readouterr().out
