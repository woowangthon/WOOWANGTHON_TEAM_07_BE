from fastapi import FastAPI

from app.errors import register_exception_handlers
from app.routers import alternatives, departure_time, elevators, health, stations


def create_app() -> FastAPI:
    app = FastAPI(title="WOOWANGTHON Backend", version="0.1.0")
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(stations.router)
    app.include_router(elevators.router)
    app.include_router(alternatives.router)
    app.include_router(departure_time.router)
    return app


app = create_app()
