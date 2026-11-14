"""시간표로 탑승 열차를 고른다. 이슈 #38 / docs/api-contract.md 8.2절.

- 환승 없는 한 노선 경로만 다룬다. 첫 구간(탑승역 -> 다음 역)의 정차 순서로 방향(INOUT_TAG 1, 2)을 정한다.
  방향 값의 의미(상행·하행, 내선·외선)는 쓰지 않는다. 순환선도 같은 방식이다.
- 같은 열차 번호가 탑승역 -> 다음 역 순서로 짧은 간격(MAX_ADJACENT_GAP_SECONDS) 안에 서는 방향을 고른다.
  두 방향 모두 맞거나 하나도 없으면 방향을 정하지 않고 None을 돌려준다.
- 도착 희망 시각에서 출구 이동을 뺀 시각까지 하차역에 닿는 열차 중 가장 늦게 탑승역을 출발하는 열차를 고른다.
- 급행은 제외한다. 계획 시각이며 지연은 반영하지 않는다. 공휴일 데이터가 없어 요일로만 서비스일 종류를 정한다.
- 서비스일은 24시를 넘는다. 도착 시각의 날짜와 전날 서비스일(+24시간)을 모두 보고 더 늦게 출발하는 열차를 고른다.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, aliased

from app.models import TrainSchedule
from app.services.routing import ADJACENT, PathNode, RoutePath
from app.services.timetable import MAX_TIME_SECONDS, SECONDS_PER_DAY, day_type_for_date
from app.timeutil import KST

MAX_ADJACENT_GAP_SECONDS = 1800


@dataclass(frozen=True)
class SelectedTrain:
    train_no: str
    direction: int
    boarding_at: datetime  # KST
    alighting_at: datetime  # KST


@dataclass(frozen=True)
class RideLeg:
    board: PathNode
    next_stop: PathNode
    alight: PathNode


def single_line_leg(path: RoutePath) -> RideLeg | None:
    """환승 없이 한 노선만 타는 경로의 탑승·다음·하차 노드. 그 밖의 경로는 None."""
    if not path.hops or any(hop.edge_type != ADJACENT for hop in path.hops):
        return None
    if len({node.line_id for node in path.nodes}) != 1:
        return None
    return RideLeg(board=path.nodes[0], next_stop=path.nodes[1], alight=path.nodes[-1])


def _service_midnight(service_date: date) -> datetime:
    return datetime.combine(service_date, time(0), tzinfo=KST)


def infer_direction(session: Session, board_sl: int, next_sl: int, day_type: str) -> int | None:
    """탑승역 -> 다음 역 순서로 같은 열차가 서는 방향. 하나로 정해지지 않으면 None."""
    board = aliased(TrainSchedule)
    nxt = aliased(TrainSchedule)
    next_time = func.coalesce(nxt.arrival_time, nxt.departure_time)
    rows = session.execute(
        select(board.direction, func.count())
        .select_from(board)
        .join(
            nxt,
            and_(
                nxt.train_no == board.train_no,
                nxt.service_day_type == board.service_day_type,
                nxt.direction == board.direction,
            ),
        )
        .where(
            board.station_line_id == board_sl,
            nxt.station_line_id == next_sl,
            board.service_day_type == day_type,
            board.is_express.is_(False),
            board.departure_time.is_not(None),
            next_time > board.departure_time,
            next_time - board.departure_time <= MAX_ADJACENT_GAP_SECONDS,
        )
        .group_by(board.direction)
    ).all()
    directions = [direction for direction, _ in rows]
    return directions[0] if len(directions) == 1 else None


def _last_train(
    session: Session, leg: RideLeg, day_type: str, direction: int, earliest_board_seconds: int, latest_alight_seconds: int
) -> tuple[str, int, int] | None:
    board = aliased(TrainSchedule)
    alight = aliased(TrainSchedule)
    alight_time = func.coalesce(alight.arrival_time, alight.departure_time)
    return session.execute(
        select(board.train_no, board.departure_time, alight_time)
        .select_from(board)
        .join(
            alight,
            and_(
                alight.train_no == board.train_no,
                alight.service_day_type == board.service_day_type,
                alight.direction == board.direction,
            ),
        )
        .where(
            board.station_line_id == leg.board.station_line_id,
            alight.station_line_id == leg.alight.station_line_id,
            board.service_day_type == day_type,
            board.direction == direction,
            board.is_express.is_(False),
            board.departure_time >= earliest_board_seconds,
            alight_time > board.departure_time,
            alight_time <= latest_alight_seconds,
        )
        .order_by(board.departure_time.desc(), board.train_no)
        .limit(1)
    ).first()


def select_train(
    session: Session,
    path: RoutePath,
    arrival_at: datetime,
    *,
    exit_walk_minutes: int,
    holidays: frozenset[date] = frozenset(),
) -> SelectedTrain | None:
    """도착 희망 시각에 맞는 막차 직전 열차. 환승 경로, 시간표 없음, 방향 불명, 열차 없음이면 None."""
    leg = single_line_leg(path)
    if leg is None:
        return None
    latest_alight = arrival_at.astimezone(KST) - timedelta(minutes=exit_walk_minutes)

    best: SelectedTrain | None = None
    for days_back in (0, 1):
        service_date = latest_alight.date() - timedelta(days=days_back)
        midnight = _service_midnight(service_date)
        latest_seconds = int((latest_alight - midnight).total_seconds())
        if not 0 < latest_seconds <= MAX_TIME_SECONDS:
            continue
        day_type = day_type_for_date(service_date, holidays)
        direction = infer_direction(session, leg.board.station_line_id, leg.next_stop.station_line_id, day_type)
        if direction is None:
            continue
        # 전날 서비스일은 자정을 넘긴 열차(24시 이후 출발)만 오늘 새벽의 열차다.
        earliest_board = SECONDS_PER_DAY if days_back else 0
        found = _last_train(session, leg, day_type, direction, earliest_board, latest_seconds)
        if found is None:
            continue
        train_no, boarding_seconds, alighting_seconds = found
        candidate = SelectedTrain(
            train_no=train_no,
            direction=direction,
            boarding_at=midnight + timedelta(seconds=boarding_seconds),
            alighting_at=midnight + timedelta(seconds=alighting_seconds),
        )
        if best is None or candidate.boarding_at > best.boarding_at:
            best = candidate
    return best
