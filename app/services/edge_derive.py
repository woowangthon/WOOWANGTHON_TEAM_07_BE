"""시간표(train_schedules)에서 인접역 간선(ADJACENT)을 뽑는다. 이슈 #16, #30, #31.

방식: 같은 열차(train_no)가 정차하는 역을 시각 순으로 정렬해 이웃한 두 역을 인접역으로 본다.
- 같은 (요일, 방향, train_no) 안에서만 묶는다. 시각이 없는 쪽(시발의 도착, 종착의 출발)은 있는 쪽 시각을 쓴다.
- 급행(is_express)은 역을 건너뛰므로 제외한다. 일반 열차 정차 순서만 쓴다.
- 같은 노선 안의 연속 정차만 간선이 된다. 노선이 다르면 건너뛴다.
- 이웃한 두 역의 시간 차가 MAX_GAP_SECONDS를 넘으면 시간표 오류로 보고 제외한다.
- 서로 다른 열차가 MIN_TRAINS번 이상 확인한 쌍만 간선으로 인정한다.
- 급행 표시가 없는 무정차 열차(회송·입출고로 추정)가 먼 역을 잇는 일이 있다. 두 역 중 더 적게 확인된 쪽의 최다 지지 간선 대비 MIN_RELATIVE_SUPPORT 미만이면 제외한다.
- 한 쌍은 한 행이다(from_station_line_id가 작은 쪽). 소비하는 쪽은 무방향으로 취급한다.
- source는 "OA-101"이다. 시간표 기반 추출이며 공식 인접역 자료는 아니다. 기존 간선은 지우거나 덮어쓰지 않는다.
- 외부 API를 호출하지 않는다.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import StationEdge, StationLine, TrainSchedule

SOURCE_TIMETABLE = "OA-101"
MAX_GAP_SECONDS = 20 * 60
MIN_TRAINS = 2
MIN_RELATIVE_SUPPORT = 0.2


@dataclass
class DeriveReport:
    dry_run: bool = False
    counts: Counter = field(default_factory=Counter)
    pairs: list[tuple[int, int]] = field(default_factory=list)


def derive_adjacent_pairs(
    session: Session, *, min_trains: int = MIN_TRAINS
) -> tuple[dict[tuple[int, int], int], Counter]:
    """(작은 station_line_id, 큰 station_line_id) -> 확인한 열차 수. 통계 Counter를 함께 돌려준다."""
    stats: Counter = Counter()
    line_of = dict(session.execute(select(StationLine.station_line_id, StationLine.line_id)).all())
    rows = session.execute(
        select(
            TrainSchedule.service_day_type,
            TrainSchedule.direction,
            TrainSchedule.train_no,
            TrainSchedule.station_line_id,
            TrainSchedule.arrival_time,
            TrainSchedule.departure_time,
            TrainSchedule.is_express,
        )
    )
    trains: dict[tuple[str, int, str], list[tuple[int, int]]] = defaultdict(list)
    for day_type, direction, train_no, sl_id, arrival, departure, is_express in rows:
        if is_express:
            stats["express_rows_skipped"] += 1
            continue
        when = departure if departure is not None else arrival
        if when is None:
            continue
        trains[(day_type, direction, train_no)].append((when, sl_id))

    support: Counter = Counter()
    for stops in trains.values():
        stops.sort()
        for (t1, a), (t2, b) in zip(stops, stops[1:]):
            if a == b:
                continue
            if line_of.get(a) != line_of.get(b):
                stats["cross_line_skipped"] += 1
                continue
            if t2 - t1 > MAX_GAP_SECONDS:
                stats["gap_too_long_skipped"] += 1
                continue
            support[(min(a, b), max(a, b))] += 1
    stats["trains_scanned"] = len(trains)
    kept = {pair: n for pair, n in support.items() if n >= min_trains}
    stats["pairs_below_min_trains"] = len(support) - len(kept)

    strongest: Counter = Counter()
    for (a, b), n in kept.items():
        strongest[a] = max(strongest[a], n)
        strongest[b] = max(strongest[b], n)
    steady = {
        (a, b): n for (a, b), n in kept.items() if n >= MIN_RELATIVE_SUPPORT * min(strongest[a], strongest[b])
    }
    stats["pairs_below_relative_support"] = len(kept) - len(steady)
    return steady, stats


def load_derived_edges(
    session: Session, *, dry_run: bool = False, min_trains: int = MIN_TRAINS, prune: bool = False
) -> DeriveReport:
    """시간표에서 뽑은 인접 간선을 적재한다. 같은 입력을 다시 넣어도 행이 늘지 않는다.

    prune=True면 이번 추출에 없는 기존 OA-101 ADJACENT 간선을 지운다(다른 source는 건드리지 않는다).
    """
    report = DeriveReport(dry_run=dry_run)
    try:
        pairs, stats = derive_adjacent_pairs(session, min_trains=min_trains)
        report.counts.update(stats)
        existing = {
            (a, b): (t, s)
            for a, b, t, s in session.execute(
                select(
                    StationEdge.from_station_line_id,
                    StationEdge.to_station_line_id,
                    StationEdge.edge_type,
                    StationEdge.source,
                )
            )
        }
        for a, b in sorted(pairs):
            found = existing.get((a, b)) or existing.get((b, a))
            if found is not None:
                if found == ("ADJACENT", SOURCE_TIMETABLE):
                    report.counts["adjacent_unchanged"] += 1
                else:
                    report.counts["adjacent_kept_other_source"] += 1  # 기존 간선은 덮어쓰지 않는다
                continue
            session.add(
                StationEdge(
                    from_station_line_id=a,
                    to_station_line_id=b,
                    edge_type="ADJACENT",
                    source=SOURCE_TIMETABLE,
                )
            )
            existing[(a, b)] = ("ADJACENT", SOURCE_TIMETABLE)
            report.counts["adjacent_created"] += 1
            report.pairs.append((a, b))
        if prune:
            wanted = {(min(a, b), max(a, b)) for a, b in pairs}
            stale = session.scalars(
                select(StationEdge).where(StationEdge.edge_type == "ADJACENT", StationEdge.source == SOURCE_TIMETABLE)
            ).all()
            for edge in stale:
                key = (
                    min(edge.from_station_line_id, edge.to_station_line_id),
                    max(edge.from_station_line_id, edge.to_station_line_id),
                )
                if key not in wanted:
                    session.delete(edge)
                    report.counts["adjacent_pruned"] += 1
        session.flush()
        if dry_run:
            session.rollback()
        else:
            session.commit()
    except Exception:
        session.rollback()
        raise
    return report
