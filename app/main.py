from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.cors import ALLOWED_HEADERS, ALLOWED_METHODS, parse_origins

from app.errors import register_exception_handlers
from app.routers import alternatives, arrivals, departure_time, elevators, health, stations


def create_app() -> FastAPI:
    app = FastAPI(title="WOOWANGTHON Backend", version="0.1.0")
    register_exception_handlers(app)
    origins = parse_origins(settings.cors_allowed_origins)
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=ALLOWED_METHODS,
            allow_headers=ALLOWED_HEADERS,
        )
    app.include_router(health.router)
    app.include_router(stations.router)
    app.include_router(elevators.router)
    app.include_router(alternatives.router)
    app.include_router(departure_time.router)
    app.include_router(arrivals.router)
    return app


app = create_app()
