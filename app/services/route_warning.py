"""경로 위 역의 엘리베이터 경고. 이슈 #37, #39 / docs/api-contract.md 8.4절.

- 판정은 status_judgment의 순수 함수를 그대로 쓴다. 확인된 설비만으로 판정한다.
- 경고 status
  - WARNING: 모든 설비가 가동 안됨(UNAVAILABLE)이거나 일부가 가동 안됨(PARTIAL)인 역이 하나라도 있다.
  - UNCONFIRMED: WARNING은 없지만 상태를 확인하지 못했거나 일부만 확인된 역이 있다.
  - CLEAR: 경로 위 모든 역이 확인된 설비 기준으로 모두 가동중이다(data_fresh=true).
- "이용 가능"이라고 단정하지 않는다. CLEAR 문구도 "확인된 설비 기준"이라고 밝힌다(계약 6.3절).
- 상태가 없는 역(설비 정보 없음)은 UNCONFIRMED다. 정상으로 추정하지 않는다.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Station
from app.schemas.elevators import ElevatorWarning, ElevatorWarningItem, StationStatusSummary
from app.services.elevator_queries import _elevator_rows, _judge
from app.services.status_judgment import (
    STATION_AVAILABLE,
    STATION_PARTIAL,
    STATION_UNAVAILABLE,
    StationJudgment,
    judge_station_without_elevators,
)
from app.services.status_messages import (
    WARN_CLEAR,
    WARN_ITEM_PARTIAL,
    WARN_ITEM_UNAVAILABLE,
    WARN_ITEM_UNCONFIRMED,
    WARN_UNCONFIRMED,
    WARN_WARNING,
)


def judge_stations_by_id(
    session: Session, station_ids: list[int], *, now: datetime, ttl_minutes: int
) -> dict[int, StationJudgment]:
    """역 id별 판정. 설비가 없는 역은 판정 불가(status None)로 채운다."""
    unique = sorted(set(station_ids))
    rows = _elevator_rows(session, station_ids=unique) if unique else []
    judged = {j.station_id: j for j in _judge(rows, now=now, ttl_minutes=ttl_minutes)} if rows else {}
    return {sid: judged.get(sid) or judge_station_without_elevators(sid) for sid in unique}


def summary_of(j: StationJudgment) -> StationStatusSummary:
    return StationStatusSummary(
        status=j.status,
        data_fresh=j.data_fresh,
        total_elevator_count=j.total_elevator_count,
        unknown_elevator_count=j.unknown_elevator_count,
    )


def build_elevator_warning(
    judgments: dict[int, StationJudgment], ordered_station_ids: list[int], names: dict[int, str]
) -> ElevatorWarning:
    """경로 순서대로 역을 훑어 경고를 만든다. 같은 역이 여러 번 나와도 한 번만 다룬다."""
    items: list[ElevatorWarningItem] = []
    has_warning = False
    seen: set[int] = set()
    for sid in ordered_station_ids:
        if sid in seen:
            continue
        seen.add(sid)
        j = judgments[sid]
        if j.status == STATION_UNAVAILABLE:
            kind, message, has_warning = "UNAVAILABLE", WARN_ITEM_UNAVAILABLE, True
        elif j.status == STATION_PARTIAL:
            kind, message, has_warning = "PARTIAL", WARN_ITEM_PARTIAL, True
        elif j.status == STATION_AVAILABLE and j.data_fresh:
            continue
        else:
            kind, message = "UNCONFIRMED", WARN_ITEM_UNCONFIRMED
        items.append(ElevatorWarningItem(station_id=sid, station_name=names[sid], kind=kind, message=message))

    if has_warning:
        return ElevatorWarning(status="WARNING", items=items, message=WARN_WARNING)
    if items:
        return ElevatorWarning(status="UNCONFIRMED", items=items, message=WARN_UNCONFIRMED)
    return ElevatorWarning(status="CLEAR", items=[], message=WARN_CLEAR)


def station_names(session: Session, station_ids: list[int]) -> dict[int, str]:
    return dict(
        session.execute(
            select(Station.station_id, Station.station_name).where(Station.station_id.in_(sorted(set(station_ids))))
        ).all()
    )
