"""서버 수명 동안 수집 CLI를 직렬 실행한다. 요청 처리와 별도 프로세스다."""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI

from app.config import settings

logger = logging.getLogger("uvicorn.error")
COLLECT_TIMEOUT_SECONDS = 300
RETRY_SECONDS = 60


def validate_refresh_settings() -> None:
    if not settings.elevator_auto_refresh:
        return
    if not settings.seoul_open_api_key.strip():
        raise ValueError("자동 수집에는 SEOUL_OPEN_API_KEY 설정이 필요합니다.")
    if settings.elevator_refresh_interval_minutes >= settings.elevator_status_ttl_minutes:
        raise ValueError("자동 수집 주기는 ELEVATOR_STATUS_TTL_MINUTES보다 짧아야 합니다.")


async def collect_once() -> bool:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.cli.collect_elevators", "--apply",
        cwd=Path(__file__).resolve().parents[2],
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(process.wait(), timeout=COLLECT_TIMEOUT_SECONDS)
        if process.returncode == 0:
            logger.info("엘리베이터 자동 수집 완료. 상세 이력: data/logs/ingestion_issues.jsonl")
            return True
        logger.warning("엘리베이터 자동 수집 종료 코드 %s. 기존 수집 CLI로 상세 오류를 확인하세요.", process.returncode)
        return False
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()


async def refresh_loop() -> None:
    failures = 0
    while True:
        try:
            success = await collect_once()
        except Exception as exc:
            # 예외 원문에는 외부 URL/키가 섞일 수 있어 종류만 기록한다.
            logger.warning("엘리베이터 자동 수집 실패(%s). 기존 상태와 TTL 판정을 유지합니다.", type(exc).__name__)
            success = False
        failures = 0 if success else failures + 1
        normal_delay = settings.elevator_refresh_interval_minutes * 60
        delay = normal_delay if success else min(RETRY_SECONDS * 2 ** min(failures - 1, 6), normal_delay)
        await asyncio.sleep(delay)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_refresh_settings()
    task = asyncio.create_task(refresh_loop()) if settings.elevator_auto_refresh else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
