"""시간표(train_schedules)에서 인접역 간선(ADJACENT)을 뽑는다. 이슈 #16, #30, #31.

방식: 같은 열차(train_no)가 정차하는 역을 시각 순으로 정렬해 이웃한 두 역을 인접역으로 본다.
- 같은 (노선, 요일, 방향, train_no) 안에서만 묶는다. 같은 번호가 다른 노선에도 있으므로 노선을 섞지 않는다(#72).
  시각이 없는 쪽(시발의 도착, 종착의 출발)은 있는 쪽 시각을 쓴다.
- 급행(is_express)은 역을 건너뛴다. 급행 표시가 한 행이라도 있는 열차는 통째로 증거에서 뺀다.
  급행 행만 빼고 앞뒤를 이으면 건너뛴 역을 사이에 둔 두 역이 인접으로 잡힌다(#72).
- 서로 다른 역의 정차 시각이 같은 열차도 순서를 알 수 없으므로 통째로 뺀다(ID 순서로 잇지 않는다, #72).
- 이웃한 두 역의 시간 차가 MAX_GAP_SECONDS를 넘으면 시간표 오류로 보고 제외한다.
- 서로 다른 열차 번호 MIN_TRAINS개 이상이 확인한 쌍만 간선으로 인정한다. 같은 번호가 요일·방향에 걸쳐
  반복되어도 한 번으로 센다(#72).
- 급행 표시가 없는 무정차 열차(회송·입출고로 추정)가 먼 역을 잇는 일이 있다. 두 역 중 더 적게 확인된 쪽의 최다 지지 간선 대비 MIN_RELATIVE_SUPPORT 미만이면 제외한다.
- 한 쌍은 한 행이다(from_station_line_id가 작은 쪽). 소비하는 쪽은 무방향으로 취급한다.
- source는 "OA-101"이다. 시간표 기반 추출이며 공식 인접역 자료는 아니다. 기존 간선은 지우거나 덮어쓰지 않는다.
- 외부 API를 호출하지 않는다.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import StationEdge, StationExternalId, StationLine, TrainSchedule
from app.services.station_seed import SOURCE_OA12035
from app.services.timetable import SERVICE_DAY_TYPES

SOURCE_TIMETABLE = "OA-101"
MAX_GAP_SECONDS = 20 * 60
MIN_TRAINS = 2
MIN_RELATIVE_SUPPORT = 0.2
# --prune은 "이번 추출에 없는 간선"을 지운다. 시간표가 일부만 있으면 멀쩡한 간선도 없는 것으로 보이므로,
# 수집 대상 역-노선의 이 비율 이상이 시간표를 갖고 요일 3종이 모두 있을 때만 허용한다.
# 비율은 신설·미운행 역에 시간표가 비는 경우를 감안한 값이며 실제 DB로 검증하지 않았다(미검증).
MIN_PRUNE_STATION_COVERAGE = 0.9


@dataclass
class TimetableCoverage:
    expected_station_lines: int
    covered_station_lines: int
    missing_day_types: tuple[str, ...]

    @property
    def ratio(self) -> float:
        return self.covered_station_lines / self.expected_station_lines if self.expected_station_lines else 0.0

    @property
    def sufficient_for_prune(self) -> bool:
        return self.ratio >= MIN_PRUNE_STATION_COVERAGE and not self.missing_day_types


def timetable_coverage(session: Session) -> TimetableCoverage:
    """collect_timetable이 받을 수 있는 역-노선(OA-12035 연결) 중 시간표가 있는 비율과 빠진 요일 종류."""
    expected = set(
        session.scalars(
            select(StationExternalId.station_line_id).where(
                StationExternalId.source == SOURCE_OA12035, StationExternalId.station_line_id.is_not(None)
            )
        )
    )
    present = set(session.scalars(select(TrainSchedule.station_line_id).distinct()))
    day_types = set(session.scalars(select(TrainSchedule.service_day_type).distinct()))
    return TimetableCoverage(
        expected_station_lines=len(expected),
        covered_station_lines=len(expected & present),
        missing_day_types=tuple(d for d in SERVICE_DAY_TYPES if d not in day_types),
    )


@dataclass
class DeriveReport:
    dry_run: bool = False
    counts: Counter = field(default_factory=Counter)
    pairs: list[tuple[int, int]] = field(default_factory=list)


def derive_adjacent_pairs(
    session: Session, *, min_trains: int = MIN_TRAINS
) -> tuple[dict[tuple[int, int], int], Counter]:
    """(작은 station_line_id, 큰 station_line_id) -> 확인한 서로 다른 열차 번호 수. 통계 Counter를 함께 돌려준다."""
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
    # 같은 열차 번호가 다른 노선에도 있으므로 노선까지 묶음 키에 넣는다.
    trains: dict[tuple[str | None, str, int, str], list[tuple[int, int]]] = defaultdict(list)
    express_groups: set[tuple[str | None, str, int, str]] = set()
    for day_type, direction, train_no, sl_id, arrival, departure, is_express in rows:
        key = (line_of.get(sl_id), day_type, direction, train_no)
        if is_express:
            stats["express_rows_skipped"] += 1
            express_groups.add(key)
            continue
        when = departure if departure is not None else arrival
        if when is None:
            continue
        trains[key].append((when, sl_id))

    # 쌍 -> 그 쌍을 이은 서로 다른 열차 번호. 쌍은 역-노선 id라 이미 노선별이다.
    support: dict[tuple[int, int], set[str]] = defaultdict(set)
    for key, stops in trains.items():
        if key in express_groups:
            # 급행 표시가 하나라도 있으면 열차 전체를 뺀다. 급행 행만 빼고 앞뒤를 이으면 건너뛴 역을 잇게 된다.
            stats["express_trains_skipped"] += 1
            continue
        stations_at: dict[int, set[int]] = defaultdict(set)
        for when, sl_id in stops:
            stations_at[when].add(sl_id)
        if any(len(ids) > 1 for ids in stations_at.values()):
            # 서로 다른 역의 정차 시각이 같으면 순서를 알 수 없다. ID 순서로 잇지 않고 열차 전체를 뺀다.
            stats["same_time_trains_skipped"] += 1
            continue
        stats["trains_scanned"] += 1
        stops.sort()
        train_no = key[3]
        for (t1, a), (t2, b) in zip(stops, stops[1:]):
            if a == b:
                continue
            if t2 - t1 > MAX_GAP_SECONDS:
                stats["gap_too_long_skipped"] += 1
                continue
            support[(min(a, b), max(a, b))].add(train_no)
    # 같은 번호가 요일·방향에 반복되어도 한 번으로 센다.
    kept = {pair: len(nos) for pair, nos in support.items() if len(nos) >= min_trains}
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
