from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.cors import ALLOWED_HEADERS, ALLOWED_METHODS, parse_origins

from app.errors import register_exception_handlers
from app.routers import alternatives, arrivals, departure_time, elevators, health, routes, stations
from app.routers import auth
from app.services.auth import validate_auth_urls


def create_app() -> FastAPI:
    validate_auth_urls()
    app = FastAPI(title="WOOWANGTHON Backend", version="0.1.0")
    register_exception_handlers(app)
    @app.middleware("http")
    async def private_account_responses(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/api/auth/", "/api/me/")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response
    origins = parse_origins(settings.cors_allowed_origins)
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=ALLOWED_METHODS,
            allow_headers=ALLOWED_HEADERS,
        )
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(stations.router)
    app.include_router(elevators.router)
    app.include_router(alternatives.router)
    app.include_router(departure_time.router)
    app.include_router(routes.router)
    app.include_router(arrivals.router)
    return app


app = create_app()
