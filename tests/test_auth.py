"""실제 인증 키·외부 네트워크 없이 OAuth HTTP 교환부터 계정 저장까지 검증한다."""
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.config import settings
from app.models.accounts import AuthIdentity, AuthSession, OAuthFlow, User, UserFavorite
from app.services import auth

ORIGIN = "http://127.0.0.1:5173"


@pytest.fixture
def oauth(monkeypatch):
    for provider in ("google", "kakao"):
        monkeypatch.setattr(settings, f"{provider}_client_id", f"test-{provider}")
        monkeypatch.setattr(settings, f"{provider}_client_secret", SecretStr("test-secret"))
    monkeypatch.setattr(settings, "auth_public_base_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(settings, "auth_frontend_url", ORIGIN + "/#/main")
    monkeypatch.setattr(settings, "auth_cookie_secure", False)
    state = {"subject": "google-one", "fail": False, "requests": []}
    original = httpx.Client

    def handle(request):
        state["requests"].append(request)
        if state["fail"]:
            return httpx.Response(400, json={"error": "synthetic-provider-error", "secret": "test-secret"})
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "synthetic-access-token"})
        assert request.headers["authorization"] == "Bearer synthetic-access-token"
        if request.url.host == "kapi.kakao.com":
            return httpx.Response(200, json={"id": 123, "properties": {"nickname": "카카오 사용자"}})
        return httpx.Response(200, json={"sub": state["subject"], "name": "구글 사용자"})

    monkeypatch.setattr(auth.httpx, "Client", lambda **options: original(transport=httpx.MockTransport(handle), **options))
    return state


def start(client, provider="google"):
    response = client.get(f"/api/auth/{provider}/login", follow_redirects=False)
    assert response.status_code == 302
    return parse_qs(urlsplit(response.headers["location"]).query)


def sign_in(client, provider="google"):
    params = start(client, provider)
    response = client.get(f"/api/auth/{provider}/callback", params={"state": params["state"][0], "code": "synthetic-code"}, follow_redirects=False)
    assert response.status_code == 303
    assert "auth_error" not in response.headers["location"]
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    return me.json()


def mutation_headers(me):
    return {"Origin": ORIGIN, "X-CSRF-Token": me["csrf_token"]}


@pytest.mark.parametrize("provider", ["google", "kakao"])
def test_oauth_exchange_session_hash_cookie_and_relogin(client, db, oauth, provider):
    me = sign_in(client, provider)
    assert me["provider"] == provider
    assert client.get("/api/auth/me").headers["cache-control"] == "no-store"
    session = db.scalar(select(AuthSession))
    assert session.token_hash == auth.digest(client.cookies.get(auth.SESSION_COOKIE))
    assert session.token_hash != client.cookies.get(auth.SESSION_COOKIE)
    token_request = oauth["requests"][0]
    form = parse_qs(token_request.content.decode())
    assert form["client_secret"] == ["test-secret"]
    assert form["redirect_uri"] == [f"http://127.0.0.1:8000/api/auth/{provider}/callback"]
    if provider == "google":
        assert len(form["code_verifier"][0]) >= 43
    assert sign_in(client, provider)["id"] == me["id"]
    assert len(db.scalars(select(User)).all()) == 1
    assert len(db.scalars(select(AuthSession)).all()) == 1


def test_state_browser_binding_provider_binding_and_single_use(client, oauth):
    params = start(client)
    state = params["state"][0]
    assert params["code_challenge_method"] == ["S256"]
    other = TestClient(client.app)
    response = other.get("/api/auth/google/callback", params={"state": state, "code": "x"}, follow_redirects=False)
    assert "invalid_state" in response.headers["location"]
    assert not oauth["requests"]
    response = client.get("/api/auth/kakao/callback", params={"state": state, "code": "x"}, follow_redirects=False)
    assert "invalid_state" in response.headers["location"]
    for expected in (False, True):
        response = client.get("/api/auth/google/callback", params={"state": state, "code": "x"}, follow_redirects=False)
        assert ("invalid_state" in response.headers["location"]) is expected
    assert len(oauth["requests"]) == 2


def test_expired_state_and_provider_denial(client, db, oauth):
    params = start(client)
    flow = db.scalar(select(OAuthFlow))
    flow.expires_at = "2000-01-01T00:00:00Z"
    db.commit()
    response = client.get("/api/auth/google/callback", params={"state": params["state"][0], "code": "x"}, follow_redirects=False)
    assert "invalid_state" in response.headers["location"]
    params = start(client)
    response = client.get("/api/auth/google/callback", params={"state": params["state"][0], "error": "access_denied"}, follow_redirects=False)
    assert "cancelled" in response.headers["location"]
    assert not oauth["requests"]


def test_provider_error_never_discloses_tokens_or_creates_user(client, db, oauth):
    oauth["fail"] = True
    params = start(client)
    response = client.get("/api/auth/google/callback", params={"state": params["state"][0], "code": "private-code"}, follow_redirects=False)
    assert "provider_failed" in response.headers["location"]
    assert "private-code" not in str(response.headers) and "test-secret" not in response.text
    assert db.scalar(select(User)) is None


def test_unconfigured_or_unknown_provider(client, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "")
    assert client.get("/api/auth/google/login").status_code == 503
    assert client.get("/api/auth/unknown/login").status_code == 404
    assert client.get("/api/auth/me").status_code == 401


def test_provider_readiness_has_only_flags_not_credentials(client, oauth):
    response = client.get('/api/auth/providers')
    assert response.status_code == 200
    assert response.json() == {'providers': {'google': {'configured': True}, 'kakao': {'configured': True}},
                               'storage_ready': True, 'limitations': []}
    assert 'test-secret' not in response.text and 'test-google' not in response.text
    assert response.headers['cache-control'] == 'no-store'


def test_unmigrated_database_returns_actionable_error_without_writes(tmp_path, oauth):
    from app.database import get_session, make_engine
    from app.main import create_app
    from sqlalchemy.orm import Session
    from sqlalchemy import inspect
    engine = make_engine(f'sqlite:///{tmp_path / "unmigrated.db"}')
    app = create_app()
    with Session(engine) as db:
        app.dependency_overrides[get_session] = lambda: db
        browser = TestClient(app)
        assert browser.get('/api/auth/providers').json()['storage_ready'] is False
        response = browser.get('/api/auth/google/login', follow_redirects=False)
        assert response.status_code == 503
        assert response.json()['error']['code'] == 'AUTH_STORAGE_NOT_READY'
        assert inspect(engine).get_table_names() == []
    engine.dispose()


def test_profile_favorites_persist_after_logout_and_relogin(client, db, oauth):
    me = sign_in(client)
    headers = mutation_headers(me)
    response = client.patch("/api/auth/profile", json={"display_name": "내 이름"}, headers=headers)
    assert response.json()["name"] == "내 이름"
    response = client.post("/api/me/favorites", json={"place_name": "역 이름 무시", "station_id": 3, "alias": "회사"}, headers=headers)
    assert response.status_code == 201
    favorite = response.json()
    assert favorite["place_name"] == "강남"
    assert client.post("/api/me/favorites", json={"place_name": "강남", "station_id": 3}, headers=headers).status_code == 409
    assert client.patch(f"/api/me/favorites/{favorite['id']}", json={"alias": "직장"}, headers=headers).status_code == 200
    old_token = client.cookies.get(auth.SESSION_COOKIE)
    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    assert db.get(AuthSession, auth.digest(old_token)) is None
    assert db.scalar(select(UserFavorite)) is not None
    assert client.get("/api/me/favorites").status_code == 401
    me = sign_in(client)
    assert me["name"] == "내 이름"
    assert client.get("/api/me/favorites").json()["items"][0]["alias"] == "직장"
    assert client.delete(f"/api/me/favorites/{favorite['id']}", headers=mutation_headers(me)).status_code == 204
    assert client.get("/api/me/favorites").json()["items"] == []


def test_users_and_providers_are_isolated(client, db, oauth):
    first = sign_in(client)
    fav = client.post("/api/me/favorites", json={"place_name": "집"}, headers=mutation_headers(first)).json()
    oauth["subject"] = "google-two"
    second = sign_in(client)
    assert first["id"] != second["id"]
    assert client.get("/api/me/favorites").json()["items"] == []
    for method, kwargs in (("patch", {"json": {"alias": "탈취"}}), ("delete", {})):
        assert getattr(client, method)(f"/api/me/favorites/{fav['id']}", headers=mutation_headers(second), **kwargs).status_code == 404
    kakao = sign_in(client, "kakao")
    assert kakao["id"] != second["id"]
    assert len(db.scalars(select(AuthIdentity)).all()) == 3


def test_csrf_missing_wrong_origin_and_expired_session(client, db, oauth):
    me = sign_in(client)
    for headers in ({}, {"Origin": ORIGIN}, {"Origin": "https://evil.example", "X-CSRF-Token": me["csrf_token"]}):
        assert client.post("/api/auth/logout", headers=headers).status_code == 403
        assert client.post("/api/me/favorites", json={"place_name": "집"}, headers=headers).status_code == 403
    session = db.scalar(select(AuthSession))
    session.expires_at = "2000-01-01T00:00:00Z"
    db.commit()
    assert client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("payload", [{"display_name": " "}, {"display_name": "x" * 31}, {"display_name": "정상", "user_id": "other"}])
def test_profile_validation(client, oauth, payload):
    me = sign_in(client)
    assert client.patch("/api/auth/profile", json=payload, headers=mutation_headers(me)).status_code == 422


def test_favorite_validation_and_missing_station(client, oauth):
    me = sign_in(client)
    for payload in ({"place_name": " "}, {"place_name": "집", "user_id": "other"}, {"place_name": "집", "alias": "x" * 21}):
        assert client.post("/api/me/favorites", json=payload, headers=mutation_headers(me)).status_code == 422
    assert client.post("/api/me/favorites", json={"place_name": "없는 역", "station_id": 99999}, headers=mutation_headers(me)).status_code == 404


def test_deployment_urls_require_https_secure_cookie(monkeypatch):
    monkeypatch.setattr(settings, "auth_public_base_url", "http://example.com")
    with pytest.raises(ValueError):
        auth.validate_auth_urls()
    monkeypatch.setattr(settings, "auth_public_base_url", "https://example.com")
    monkeypatch.setattr(settings, "auth_cookie_secure", False)
    with pytest.raises(ValueError):
        auth.validate_auth_urls()
