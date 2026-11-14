"""Google·카카오 OAuth 및 쿠키 기반 계정 API."""
import secrets
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_session
from app.errors import AppError
from app.models import Station
from app.models.accounts import AuthIdentity, AuthSession, OAuthFlow, User, UserFavorite
from app.services.auth import (
    PROVIDERS, SESSION_COOKIE, callback_url, challenge, cookie_options, credentials,
    current_session, digest, exchange_identity, mutation_session, utc_after,
)

router = APIRouter(prefix="/api", tags=["accounts"])


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


def frontend_redirect(error: str | None = None):
    url = urlsplit(settings.auth_frontend_url)
    response = RedirectResponse(urlunsplit(url._replace(query=urlencode({"auth_error": error}) if error else "")), 303)
    no_store(response)
    return response


@router.get("/auth/{provider}/login")
def login(provider: str, db: Session = Depends(get_session)):
    client_id, _ = credentials(provider)
    state, browser, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    db.execute(delete(OAuthFlow).where(OAuthFlow.expires_at <= utc_after()))
    db.execute(delete(AuthSession).where(AuthSession.expires_at <= utc_after()))
    db.add(OAuthFlow(state_hash=digest(state), browser_hash=digest(browser), provider=provider,
                     verifier=verifier, expires_at=utc_after(minutes=10)))
    db.commit()
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": callback_url(provider), "state": state}
    if provider == "google":
        params.update(scope="openid profile", code_challenge=challenge(verifier), code_challenge_method="S256")
    response = RedirectResponse(PROVIDERS[provider][0] + "?" + urlencode(params), 302)
    response.set_cookie(f"woowang_oauth_{provider}", browser, max_age=600, **cookie_options())
    no_store(response)
    return response


@router.get("/auth/{provider}/callback")
def callback(provider: str, request: Request, db: Session = Depends(get_session)):
    credentials(provider)
    state = request.query_params.get("state", "")
    browser = request.cookies.get(f"woowang_oauth_{provider}", "")
    # DELETE RETURNING으로 같은 state를 동시에 두 번 소비할 수 없게 한다.
    flow = db.execute(delete(OAuthFlow).where(
        OAuthFlow.state_hash == digest(state), OAuthFlow.browser_hash == digest(browser),
        OAuthFlow.provider == provider, OAuthFlow.expires_at > utc_after(),
    ).returning(OAuthFlow.verifier)).scalar_one_or_none() if state and browser else None
    db.commit()
    if flow is None:
        return frontend_redirect("invalid_state")
    code = request.query_params.get("code", "")
    if request.query_params.get("error") or not code:
        response = frontend_redirect("cancelled")
    else:
        try:
            subject, name = exchange_identity(provider, code, flow)
        except AppError:
            response = frontend_redirect("provider_failed")
        else:
            identity = db.scalar(select(AuthIdentity).where(AuthIdentity.provider == provider, AuthIdentity.provider_subject == subject))
            if identity is None:
                user = User(id=str(uuid4()), display_name=name, created_at=utc_after(), updated_at=utc_after())
                db.add(user)
                db.flush()
                identity = AuthIdentity(id=str(uuid4()), user_id=user.id, provider=provider, provider_subject=subject)
                db.add(identity)
                try:
                    db.flush()
                except IntegrityError:
                    db.rollback()
                    identity = db.scalar(select(AuthIdentity).where(AuthIdentity.provider == provider, AuthIdentity.provider_subject == subject))
                    if identity is None:
                        raise
            old = request.cookies.get(SESSION_COOKIE)
            if old:
                db.execute(delete(AuthSession).where(AuthSession.token_hash == digest(old)))
            token = secrets.token_urlsafe(32)
            db.add(AuthSession(token_hash=digest(token), user_id=identity.user_id, provider=provider,
                               csrf_token=secrets.token_urlsafe(32), created_at=utc_after(), expires_at=utc_after(days=settings.auth_session_days)))
            db.commit()
            response = frontend_redirect()
            response.set_cookie(SESSION_COOKIE, token, max_age=settings.auth_session_days * 86400, **cookie_options())
    response.delete_cookie(f"woowang_oauth_{provider}", **cookie_options())
    return response


@router.get("/auth/me")
def me(response: Response, session: AuthSession = Depends(current_session), db: Session = Depends(get_session)):
    user = db.get(User, session.user_id)
    no_store(response)
    return {"id": user.id, "name": user.display_name, "provider": session.provider, "csrf_token": session.csrf_token, "limitations": []}


@router.post("/auth/logout", status_code=204)
def logout(response: Response, session: AuthSession = Depends(mutation_session), db: Session = Depends(get_session)):
    db.delete(session)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, **cookie_options())
    no_store(response)


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    display_name: str = Field(min_length=1, max_length=30)


@router.patch("/auth/profile")
def profile(body: ProfileInput, response: Response, session: AuthSession = Depends(mutation_session), db: Session = Depends(get_session)):
    user = db.get(User, session.user_id)
    user.display_name, user.updated_at = body.display_name, utc_after()
    db.commit()
    return me(response, session, db)


class FavoriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    place_name: str = Field(min_length=1, max_length=64)
    station_id: int | None = Field(default=None, gt=0)
    alias: str | None = Field(default=None, max_length=20)


class FavoritePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    alias: str | None = Field(default=None, max_length=20)


def favorite_json(fav: UserFavorite):
    return {"id": fav.id, "place_name": fav.place_name, "station_id": fav.station_id, "alias": fav.alias}


@router.get("/me/favorites")
def favorites(response: Response, session: AuthSession = Depends(current_session), db: Session = Depends(get_session)):
    no_store(response)
    items = db.scalars(select(UserFavorite).where(UserFavorite.user_id == session.user_id).order_by(UserFavorite.created_at, UserFavorite.id)).all()
    return {"items": [favorite_json(fav) for fav in items], "limitations": []}


@router.post("/me/favorites", status_code=201)
def add_favorite(body: FavoriteInput, response: Response, session: AuthSession = Depends(mutation_session), db: Session = Depends(get_session)):
    place_name = body.place_name
    if body.station_id is not None:
        station = db.get(Station, body.station_id)
        if station is None:
            raise AppError(404, "NOT_FOUND", "등록되지 않은 역이에요.")
        place_name = station.station_name
    fav = UserFavorite(id=str(uuid4()), user_id=session.user_id, station_id=body.station_id,
                       place_name=place_name, alias=body.alias, created_at=utc_after(), updated_at=utc_after())
    db.add(fav)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "DUPLICATE_FAVORITE", "이미 즐겨찾기에 저장한 역이에요.") from None
    no_store(response)
    return {**favorite_json(fav), "limitations": []}


def owned_favorite(favorite_id: str, session: AuthSession, db: Session):
    fav = db.scalar(select(UserFavorite).where(UserFavorite.id == favorite_id, UserFavorite.user_id == session.user_id))
    if fav is None:
        raise AppError(404, "NOT_FOUND", "즐겨찾기를 찾을 수 없어요.")
    return fav


@router.patch("/me/favorites/{favorite_id}")
def rename_favorite(favorite_id: str, body: FavoritePatch, response: Response,
                    session: AuthSession = Depends(mutation_session), db: Session = Depends(get_session)):
    fav = owned_favorite(favorite_id, session, db)
    fav.alias, fav.updated_at = body.alias, utc_after()
    db.commit()
    no_store(response)
    return {**favorite_json(fav), "limitations": []}


@router.delete("/me/favorites/{favorite_id}", status_code=204)
def remove_favorite(favorite_id: str, response: Response, session: AuthSession = Depends(mutation_session), db: Session = Depends(get_session)):
    db.delete(owned_favorite(favorite_id, session, db))
    db.commit()
    no_store(response)
