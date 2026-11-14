"""시간표에서 추출한 인접 간선(OA-101)까지 포함한 연결성 점검(#31).

시드 파일과 소형 시간표 픽스처를 임시 SQLite에만 넣고 data/dev.db는 열지 않는다.
픽스처는 실제 시간표가 아니라 규칙 확인용이다: 5호선 강동 분기, 2호선 성수 지선, 천호(5·8호선)와 건대입구(2·7호선) 환승,
그리고 급행 표시가 없는 2정차 열차가 만드는 잘못된 간선을 담는다.
"""

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import StationEdge, StationExternalId, StationLine, TrainSchedule
from app.services.edge_connectivity import connectivity_report
from app.services.edge_derive import SOURCE_TIMETABLE, derive_adjacent_pairs, load_derived_edges
from app.services.edge_seed import load_edges, read_provisional_edges, read_verified_edges
from app.services.routing import find_route
from app.services.station_seed import DEFAULT_SEED_DIR, load_seed, read_seed_dir

TRANSFER = "TRANSFER"
FETCHED_AT = "2026-11-14T03:00:00Z"
TRAINS_PER_GROUP = 12  # 상대 지지 비율(20%) 기준을 넘기려고 충분히 많은 열차를 둔다

# 역번호(OA-12035)로 적은 정차 순서. 5호선 2548 천호, 2549 강동, 2550 길동, 2551 굽은다리, 2552 명일, 2553 고덕,
# 2554 상일동, 2555 둔촌동 / 8호선 2812 천호, 2813 강동구청 / 2호선 0210 뚝섬, 0211 성수, 0212 건대입구, 0244 용답, 0245 신답
# / 7호선 2728 어린이대공원, 2729 건대입구, 2730 자양
GROUPS = {
    "L5-MAIN": ["2548", "2549", "2550", "2551", "2552", "2553", "2554"],
    "L5-BRANCH": ["2549", "2555"],
    "L8": ["2812", "2813"],
    "L2-MAIN": ["0210", "0211", "0212"],
    "L2-BRANCH": ["0211", "0244", "0245"],
    "L7": ["2728", "2729", "2730"],
}
# 중간역에 서지 않는 2정차 열차(회송·입출고로 추정). 상일동→강동은 실제 인접역이 아니다.
CONTAMINANT = ["2554", "2549"]
CONTAMINANT_TRAINS = 2


@pytest.fixture
def loaded(engine):
    with Session(engine) as session:
        rows, links, review = read_seed_dir(DEFAULT_SEED_DIR)
        load_seed(session, rows, links, review)
        load_edges(session, read_verified_edges(DEFAULT_SEED_DIR))
        load_edges(session, read_provisional_edges(DEFAULT_SEED_DIR), allow_provisional=True)
        yield session


def _sl_by_code(session) -> dict[str, int]:
    return dict(
        session.execute(
            select(StationExternalId.external_station_code, StationExternalId.station_line_id).where(
                StationExternalId.source == "OA-12035"
            )
        ).all()
    )


def _add_trains(session, codes: list[str], prefix: str, count: int) -> None:
    sl = _sl_by_code(session)
    for i in range(count):
        start = 6 * 3600 + i * 600
        for step, code in enumerate(codes):
            when = start + step * 120
            session.add(
                TrainSchedule(
                    station_line_id=sl[code], service_day_type="WEEKDAY", direction=1, train_no=f"{prefix}{i:02d}",
                    arrival_time=when, departure_time=when, is_express=False, fetched_at=FETCHED_AT,
                )
            )
    session.commit()


def _add_fixture_timetable(session) -> None:
    for prefix, codes in GROUPS.items():
        _add_trains(session, codes, prefix, TRAINS_PER_GROUP)
    _add_trains(session, CONTAMINANT, "BAD", CONTAMINANT_TRAINS)


def _pair(sl: dict[str, int], a: str, b: str) -> tuple[int, int]:
    return (min(sl[a], sl[b]), max(sl[a], sl[b]))


def _expected_pairs(session) -> set[tuple[int, int]]:
    sl = _sl_by_code(session)
    return {_pair(sl, a, b) for codes in GROUPS.values() for a, b in zip(codes, codes[1:])}


def _adjacent(session) -> dict[tuple[int, int], str | None]:
    return {
        (min(a, b), max(a, b)): source
        for a, b, source in session.execute(
            select(StationEdge.from_station_line_id, StationEdge.to_station_line_id, StationEdge.source).where(
                StationEdge.edge_type == "ADJACENT"
            )
        )
    }


def _station_id(session, code: str) -> int:
    return session.scalar(
        select(StationLine.station_id)
        .join(StationExternalId, StationExternalId.station_line_id == StationLine.station_line_id)
        .where(StationExternalId.source == "OA-12035", StationExternalId.external_station_code == code)
    )


def _same_component(session, codes: list[str]) -> bool:
    """ADJACENT 간선만으로 주어진 역들이 한 덩어리인지(환승 간선은 쓰지 않는다)."""
    sl = _sl_by_code(session)
    wanted = {sl[c] for c in codes}
    adjacency: dict[int, set[int]] = {}
    for a, b in _adjacent(session):
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    start = next(iter(wanted))
    seen, frontier = {start}, [start]
    while frontier:
        for n in adjacency.get(frontier.pop(), ()):
            if n not in seen:
                seen.add(n)
                frontier.append(n)
    return wanted <= seen


def test_derive_keeps_branches_and_drops_the_two_stop_train(loaded):
    _add_fixture_timetable(loaded)

    pairs, stats = derive_adjacent_pairs(loaded)

    sl = _sl_by_code(loaded)
    assert set(pairs) == _expected_pairs(loaded)  # 분기·지선·환승역 양쪽 노선 간선만 남는다
    assert _pair(sl, "2554", "2549") not in pairs  # 상일동–강동은 2정차 열차가 만든 잘못된 간선
    assert stats["pairs_below_relative_support"] == 1


def test_timetable_edges_add_to_seed_edges_without_overwriting_them(loaded):
    before = _adjacent(loaded)
    before_report = connectivity_report(loaded)
    _add_fixture_timetable(loaded)

    report = load_derived_edges(loaded)

    after = _adjacent(loaded)
    assert {pair: src for pair, src in after.items() if pair in before} == before  # 시드 간선과 출처가 그대로다
    assert set(after) - set(before) <= _expected_pairs(loaded)
    assert {pair for pair, src in after.items() if src == SOURCE_TIMETABLE} <= _expected_pairs(loaded)
    assert report.counts["adjacent_created"] == len(set(after) - set(before))
    assert _pair(_sl_by_code(loaded), "2554", "2549") not in after
    for line, info in connectivity_report(loaded).items():
        assert len(info.component_sizes) <= len(before_report[line].component_sizes), f"{line}호선 연결성이 나빠졌다"
    assert load_derived_edges(loaded).counts["adjacent_created"] == 0  # 다시 넣어도 늘지 않는다


def test_timetable_edges_alone_connect_each_branch_and_line(loaded):
    """시드의 인접 간선을 모두 지워도 시간표 간선만으로 분기·지선이 한 덩어리가 된다."""
    loaded.execute(delete(StationEdge).where(StationEdge.edge_type == "ADJACENT"))
    loaded.commit()
    _add_fixture_timetable(loaded)
    assert not _same_component(loaded, GROUPS["L5-MAIN"])

    report = load_derived_edges(loaded)

    assert report.counts["adjacent_created"] == len(_expected_pairs(loaded))
    assert {src for src in _adjacent(loaded).values()} == {SOURCE_TIMETABLE}
    assert _same_component(loaded, GROUPS["L5-MAIN"] + GROUPS["L5-BRANCH"])  # 강동 분기(길동·둔촌동·천호 방면)
    assert _same_component(loaded, GROUPS["L2-MAIN"] + GROUPS["L2-BRANCH"])  # 성수 지선(용답·신답)
    assert _same_component(loaded, GROUPS["L7"])
    assert _same_component(loaded, GROUPS["L8"])
    assert _pair(_sl_by_code(loaded), "2554", "2549") not in _adjacent(loaded)  # 2정차 열차 간선은 들어오지 않는다


def test_routes_cross_transfer_stations_using_only_timetable_edges(loaded):
    loaded.execute(delete(StationEdge).where(StationEdge.edge_type == "ADJACENT"))
    loaded.commit()
    _add_fixture_timetable(loaded)
    load_derived_edges(loaded)

    cases = {
        "천호 환승(5호선 상일동 → 8호선 강동구청)": ("2554", "2813"),
        "건대입구 환승(2호선 용답 → 7호선 자양)": ("0244", "2730"),
    }
    for label, (start, end) in cases.items():
        forward = find_route(loaded, _station_id(loaded, start), _station_id(loaded, end), segment_minutes=2, transfer_minutes=5)
        backward = find_route(loaded, _station_id(loaded, end), _station_id(loaded, start), segment_minutes=2, transfer_minutes=5)
        assert forward is not None and backward is not None, label
        assert forward.total_minutes == backward.total_minutes, label
        assert [h.edge_type for h in forward.hops].count(TRANSFER) == 1, label


def test_two_stop_train_alone_cannot_connect_stations(loaded):
    """정상 열차가 없는 구간은 2정차 열차 몇 대만으로 이어지지 않는다(최소 열차 수 미만)."""
    loaded.execute(delete(StationEdge).where(StationEdge.edge_type == "ADJACENT"))
    loaded.commit()
    _add_trains(loaded, CONTAMINANT, "BAD", 1)

    report = load_derived_edges(loaded)

    assert report.counts["adjacent_created"] == 0
    assert _adjacent(loaded) == {}
