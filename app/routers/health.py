from fastapi import APIRouter, Depends
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_session
from app.errors import AppError
from app.schemas.health import HealthResponse
from app.services.readiness import check_readiness

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """서버가 떠 있는지 확인한다. DB나 외부 API는 확인하지 않는다."""
    return HealthResponse(status="ok")


@router.get("/ready", tags=["health"])
def ready(db: Session = Depends(get_session)) -> dict:
    """스키마 준비 여부와 상태 수집 현황. STALE은 API가 안전하게 표현하므로 기동 실패가 아니다."""
    try:
        return check_readiness(db)
    except SQLAlchemyError:
        raise AppError(503, "DATABASE_NOT_READY", "DB에 연결할 수 없습니다. 서버 설정과 DB 상태를 확인해 주세요.") from None
