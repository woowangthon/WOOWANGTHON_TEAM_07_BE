"""OAuth 코드 교환과 세션 검증. 제공자 토큰은 요청 중에만 사용한다."""
import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, Request
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.config import settings
from app.cors import parse_origins
from app.database import get_session
from app.errors import AppError
from app.models.accounts import AuthSession

SESSION_COOKIE = "woowang_session"
ACCOUNT_TABLES = {"users", "auth_identities", "auth_sessions", "oauth_flows", "user_favorites"}
PROVIDERS = {
    "google": ("https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token", "https://openidconnect.googleapis.com/v1/userinfo"),
    "kakao": ("https://kauth.kakao.com/oauth/authorize", "https://kauth.kakao.com/oauth/token", "https://kapi.kakao.com/v2/user/me"),
}


def utc_after(**delta) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def validate_auth_urls() -> None:
    for value in (settings.auth_public_base_url, settings.auth_frontend_url):
        url = urlsplit(value)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query:
            raise ValueError("인증 URL은 사용자 정보·쿼리가 없는 절대 HTTP(S) 주소여야 합니다.")
        if url.scheme != "https" and url.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("루프백 개발 환경 외에는 인증 URL에 HTTPS가 필요합니다.")
        if url.hostname not in ("127.0.0.1", "localhost", "::1") and not settings.auth_cookie_secure:
            raise ValueError("배포 환경에서는 AUTH_COOKIE_SECURE=true가 필요합니다.")
    public = urlsplit(settings.auth_public_base_url)
    if public.path.rstrip("/") or public.fragment:
        raise ValueError("AUTH_PUBLIC_BASE_URL에는 경로·프래그먼트를 넣지 않습니다.")


def credentials(provider: str) -> tuple[str, str]:
    if provider not in PROVIDERS:
        raise AppError(404, "NOT_FOUND", "지원하지 않는 로그인 제공자입니다.")
    client_id = getattr(settings, f"{provider}_client_id").strip()
    secret = getattr(settings, f"{provider}_client_secret").get_secret_value().strip()
    if not client_id or (provider == "google" and not secret):
        raise AppError(503, "AUTH_NOT_CONFIGURED", "로그인 인증 키가 아직 설정되지 않았어요.")
    return client_id, secret


def provider_configured(provider: str) -> bool:
    try:
        credentials(provider)
        return True
    except AppError:
        return False


def storage_ready(db: Session) -> bool:
    # DB를 자동 변경하지 않는다. 미적용 DB에서도 로그인 준비 상태를 안전하게 알려 준다.
    return ACCOUNT_TABLES <= set(inspect(db.connection()).get_table_names())


def require_storage(db: Session) -> None:
    if not storage_ready(db):
        raise AppError(503, "AUTH_STORAGE_NOT_READY", "로그인 저장소가 아직 준비되지 않았어요. 관리자에게 확인해 주세요.")


def callback_url(provider: str) -> str:
    return settings.auth_public_base_url.rstrip("/") + f"/api/auth/{provider}/callback"


def exchange_identity(provider: str, code: str, verifier: str) -> tuple[str, str]:
    client_id, client_secret = credentials(provider)
    data = {"grant_type": "authorization_code", "client_id": client_id,
            "redirect_uri": callback_url(provider), "code": code}
    if client_secret:
        data["client_secret"] = client_secret
    if provider == "google":
        data["code_verifier"] = verifier
    try:
        # URL은 서버 상수다. 리다이렉트·환경 프록시로 비밀이 다른 호스트에 전달되지 않게 한다.
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            token = client.post(PROVIDERS[provider][1], data=data)
            token.raise_for_status()
            access_token = token.json()["access_token"]
            if not isinstance(access_token, str) or not access_token:
                raise ValueError("missing access token")
            profile = client.get(PROVIDERS[provider][2], headers={"Authorization": f"Bearer {access_token}"})
            profile.raise_for_status()
            info = profile.json()
            if provider == "google":
                subject, name = info.get("sub"), info.get("name")
                if not isinstance(subject, str) or not subject:
                    raise ValueError("invalid subject")
            else:
                subject = info.get("id")
                if type(subject) is not int or subject <= 0:
                    raise ValueError("invalid subject")
                name = (info.get("properties") or {}).get("nickname")
            if len(str(subject)) > 255:
                raise ValueError("invalid subject")
            return str(subject), (name.strip()[:30] if isinstance(name, str) and name.strip() else "사용자")
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        # 제공자 응답·인가 코드·키·토큰을 오류나 로그에 싣지 않는다.
        raise AppError(502, "OAUTH_FAILED", "로그인 제공자 확인에 실패했어요. 다시 시도해 주세요.") from None


def cookie_options() -> dict:
    return {"httponly": True, "secure": settings.auth_cookie_secure, "samesite": "lax", "path": "/"}


def current_session(request: Request, db: Session = Depends(get_session)) -> AuthSession:
    token = request.cookies.get(SESSION_COOKIE, "")
    session = db.get(AuthSession, digest(token)) if token else None
    if session is None or session.expires_at <= utc_after():
        raise AppError(401, "UNAUTHENTICATED", "로그인이 필요해요.")
    return session


def mutation_session(request: Request, session: AuthSession = Depends(current_session)) -> AuthSession:
    frontend = urlsplit(settings.auth_frontend_url)
    origins = {f"{frontend.scheme}://{frontend.netloc}", settings.auth_public_base_url.rstrip("/"),
               *parse_origins(settings.cors_allowed_origins)}
    csrf = request.headers.get("X-CSRF-Token", "")
    if request.headers.get("Origin") not in origins or not secrets.compare_digest(csrf, session.csrf_token):
        raise AppError(403, "CSRF_FAILED", "요청을 확인하지 못했어요. 화면을 새로고침해 주세요.")
    return session
