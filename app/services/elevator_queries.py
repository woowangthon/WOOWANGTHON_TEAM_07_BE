"""엘리베이터 조회 서비스. docs/api-contract.md 4절, 5절, 6절을 따른다.

- DB 조회는 설비·상태·역을 한 번의 SELECT로 읽는다(N+1 방지).
- 판정은 status_judgment의 순수 함수를 그대로 쓴다. 여기서는 DB 행을 입력으로 바꾸고
  결과를 응답 스키마로 옮긴다.
- 시각: 판정은 UTC로 하고, 응답의 fetched_at만 KST로 바꾼다(0.2절).
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import CODE_NOT_FOUND, AppError
from app.models import Elevator, ElevatorStatus, Station
from app.schemas.elevators import (
    ElevatorItem,
    ElevatorStatusItem,
    ElevatorStatusListResponse,
    StationElevatorsResponse,
    StationRef,
    StationStatusSummary,
)
from app.services.status_judgment import (
    ElevatorJudgment,
    ElevatorRef,
    StatusRow,
    judge_station_without_elevators,
    judge_stations,
)
from app.timeutil import utc_str_to_kst_iso

NOT_FOUND_MESSAGE = "요청한 리소스를 찾을 수 없습니다."


def _elevator_rows(
    session: Session, station_id: int | None = None, station_ids: list[int] | None = None
):
    """설비, 역 이름, 최신 상태(없으면 None)를 한 번에 읽는다. 정렬은 station_id, elevator_id."""
    stmt = (
        select(Elevator, Station.station_name, ElevatorStatus)
        .join(Station, Station.station_id == Elevator.station_id)
        .outerjoin(ElevatorStatus, ElevatorStatus.elevator_id == Elevator.elevator_id)
        .order_by(Elevator.station_id, Elevator.elevator_id)
    )
    if station_id is not None:
        stmt = stmt.where(Elevator.station_id == station_id)
    if station_ids is not None:
        stmt = stmt.where(Elevator.station_id.in_(station_ids))
    return list(session.execute(stmt).all())


def _judge(rows, *, now: datetime, ttl_minutes: int):
    refs = [ElevatorRef(elevator_id=e.elevator_id, station_id=e.station_id) for e, _, _ in rows]
    statuses = [
        StatusRow(elevator_id=s.elevator_id, status=s.status, fetched_at=s.fetched_at)
        for _, _, s in rows
        if s is not None
    ]
    return judge_stations(refs, statuses, now=now, ttl_minutes=ttl_minutes)


def _to_kst(value: str | None) -> str | None:
    return utc_str_to_kst_iso(value) if value is not None else None


def _elevator_item(judgment: ElevatorJudgment, elevator: Elevator) -> ElevatorItem:
    return ElevatorItem(
        elevator_id=elevator.elevator_id,
        elevator_name=elevator.elevator_name,
        location_description=elevator.location_description,
        operation_section=elevator.operation_section,
        status=judgment.status,
        data_fresh=judgment.data_fresh,
        unknown_reason=judgment.unknown_reason,
        observed_at=None,  # OA-15994에 원본 관측 시각이 없다(계약 4.4절, 9절)
        fetched_at=_to_kst(judgment.fetched_at),
    )


def get_station_elevators(
    session: Session, station_id: int, *, now: datetime, ttl_minutes: int
) -> StationElevatorsResponse:
    """역 한 곳의 엘리베이터 목록과 역 상태. 0 이하이거나 없는 역은 404다(계약 3.4절과 같은 규칙)."""
    if station_id < 1:
        raise AppError(404, CODE_NOT_FOUND, NOT_FOUND_MESSAGE)
    station = session.get(Station, station_id)
    if station is None:
        raise AppError(404, CODE_NOT_FOUND, NOT_FOUND_MESSAGE)

    rows = _elevator_rows(session, station_id)
    if rows:
        station_judgment = _judge(rows, now=now, ttl_minutes=ttl_minutes)[0]
    else:
        station_judgment = judge_station_without_elevators(station_id)

    by_id = {e.elevator_id: e for e, _, _ in rows}
    return StationElevatorsResponse(
        station=StationRef(station_id=station.station_id, station_name=station.station_name),
        station_status=StationStatusSummary(
            status=station_judgment.status,
            data_fresh=station_judgment.data_fresh,
            total_elevator_count=station_judgment.total_elevator_count,
            unknown_elevator_count=station_judgment.unknown_elevator_count,
        ),
        limitations=list(station_judgment.limitations),
        elevators=[_elevator_item(j, by_id[j.elevator_id]) for j in station_judgment.elevators],
    )


def list_elevator_statuses(
    session: Session, *, status: str | None, now: datetime, ttl_minutes: int
) -> ElevatorStatusListResponse:
    """전체 설비 상태 목록. status를 주면 확인된 설비 중 그 값과 같은 것만 남긴다.

    정보 없음(NO_DATA)과 오래됨(STALE) 설비는 status가 None이므로 어떤 필터에도 들어가지 않는다.
    """
    rows = _elevator_rows(session)
    if not rows:
        return ElevatorStatusListResponse(elevators=[], count=0, limitations=[])

    info = {e.elevator_id: (e, name) for e, name, _ in rows}
    judgments = [
        j for station in _judge(rows, now=now, ttl_minutes=ttl_minutes) for j in station.elevators
    ]
    if status is not None:
        judgments = [j for j in judgments if j.status == status]

    items = []
    for j in judgments:
        elevator, station_name = info[j.elevator_id]
        items.append(
            ElevatorStatusItem(
                elevator_id=elevator.elevator_id,
                elevator_name=elevator.elevator_name,
                station_id=elevator.station_id,
                station_name=station_name,
                status=j.status,
                data_fresh=j.data_fresh,
                unknown_reason=j.unknown_reason,
                observed_at=None,
                fetched_at=_to_kst(j.fetched_at),
            )
        )
    # limitations: 설비 단위 표준 문구가 계약에 없어 빈 배열이다(남은 문제 참고).
    return ElevatorStatusListResponse(elevators=items, count=len(items), limitations=[])
