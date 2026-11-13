from fastapi import APIRouter

from app.schemas.health import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """서버가 떠 있는지 확인한다. DB나 외부 API는 확인하지 않는다."""
    return HealthResponse(status="ok")
