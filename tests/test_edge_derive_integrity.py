"""시간표 인접 간선 추출이 잘못된 증거를 간선으로 만들지 않는지 확인한다(이슈 #72).

- 같은 열차 번호가 다른 노선에 있어도 노선별로 따로 묶는다.
- 급행 표시가 섞인 열차는 통째로 증거에서 뺀다(급행 행을 빼고 앞뒤를 잇지 않는다).
- 서로 다른 역의 정차 시각이 같은 열차는 순서를 알 수 없으므로 뺀다.
- 같은 열차 번호가 요일·방향에 걸쳐 반복되어도 지지 열차 수는 서로 다른 열차 번호 수로 센다.

conftest의 임시 SQLite만 쓴다. 외부 API·data/dev.db를 건드리지 않는다.
"""

from sqlalchemy import select

from app.models import StationLine, TrainSchedule
from app.services.edge_derive import derive_adjacent_pairs, load_derived_edges

FETCHED_AT = "2026-11-14T03:00:00Z"
BASE = 6 * 3600


def sl_id(db, station_id, line="2"):
    return db.scalar(
        select(StationLine.station_line_id).where(StationLine.station_id == station_id, StationLine.line_id == line)
    )


def pair(a, b):
    return (min(a, b), max(a, b))


def add_train(db, train_no, stops, *, day="WEEKDAY", direction=1):
    """stops: (station_line_id, 시각 초, 급행 여부) 목록. 도착·출발을 같은 시각으로 넣는다."""
    for sl, when, express in stops:
        db.add(
            TrainSchedule(
                station_line_id=sl, service_day_type=day, direction=direction, train_no=train_no,
                arrival_time=when, departure_time=when, is_express=express, fetched_at=FETCHED_AT,
            )
        )


def test_same_train_number_on_another_line_does_not_break_edges(db):
    # 2호선 강남(3)-교대(5)-서울대입구(6)과 3호선 교대(5)-종로3가(7)에 같은 번호의 열차가 시각이 엇갈려 다닌다.
    a, b, c = sl_id(db, 3), sl_id(db, 5), sl_id(db, 6)
    b3, d3 = sl_id(db, 5, "3"), sl_id(db, 7, "3")
    for i, train in enumerate(("2001", "2002")):
        t = BASE + i * 600
        add_train(db, train, [(a, t, False), (b, t + 120, False), (c, t + 240, False)])
        add_train(db, train, [(b3, t + 60, False), (d3, t + 180, False)])
    db.commit()

    pairs, stats = derive_adjacent_pairs(db)

    assert pairs == {pair(a, b): 2, pair(b, c): 2, pair(b3, d3): 2}
    assert stats["cross_line_skipped"] == 0


def test_mixed_express_train_is_excluded_without_stitching(db):
    # 교대(5) 행에만 급행 표시가 있는 열차. 교대 행만 빼면 강남-서울대입구가 인접으로 잘못 잡힌다.
    a, b, c = sl_id(db, 3), sl_id(db, 5), sl_id(db, 6)
    for i, train in enumerate(("2001", "2002")):
        t = BASE + i * 600
        add_train(db, train, [(a, t, False), (b, t + 120, True), (c, t + 240, False)])
    db.commit()

    pairs, stats = derive_adjacent_pairs(db)

    assert pair(a, c) not in pairs
    assert pairs == {}
    assert stats["express_trains_skipped"] == 2


def test_mixed_express_train_does_not_add_bypass_next_to_normal_trains(db):
    a, b, c = sl_id(db, 3), sl_id(db, 5), sl_id(db, 6)
    for i, train in enumerate(("2001", "2002", "2003")):
        t = BASE + i * 600
        add_train(db, train, [(a, t, False), (b, t + 120, False), (c, t + 240, False)])
    for i, train in enumerate(("2101", "2102")):
        t = 8 * 3600 + i * 600
        add_train(db, train, [(a, t, False), (b, t + 120, True), (c, t + 240, False)])
    db.commit()

    pairs, _ = derive_adjacent_pairs(db)

    assert pairs == {pair(a, b): 3, pair(b, c): 3}


def test_different_stations_with_same_time_are_not_ordered_by_id(db):
    # 교대(5)와 서울대입구(6)의 정차 시각이 같다. ID 순서로 이으면 0초 간선이 생긴다.
    a, b, c = sl_id(db, 3), sl_id(db, 5), sl_id(db, 6)
    for i, train in enumerate(("2001", "2002")):
        t = BASE + i * 600
        add_train(db, train, [(a, t, False), (c, t + 120, False), (b, t + 120, False)])
    db.commit()

    pairs, stats = derive_adjacent_pairs(db)

    assert pairs == {}
    assert pair(b, c) not in pairs and pair(a, b) not in pairs
    assert stats["same_time_trains_skipped"] == 2


def test_repeated_train_number_across_day_types_and_directions_counts_once(db):
    a, b = sl_id(db, 3), sl_id(db, 5)
    for day in ("WEEKDAY", "SATURDAY", "HOLIDAY"):
        add_train(db, "2001", [(a, BASE, False), (b, BASE + 120, False)], day=day)
    add_train(db, "2001", [(b, BASE, False), (a, BASE + 120, False)], direction=2)
    db.commit()

    pairs, stats = derive_adjacent_pairs(db)

    assert pairs == {}
    assert stats["pairs_below_min_trains"] == 1
    assert derive_adjacent_pairs(db, min_trains=1)[0] == {pair(a, b): 1}


def test_two_distinct_train_numbers_on_different_days_still_count_as_two(db):
    a, b = sl_id(db, 3), sl_id(db, 5)
    add_train(db, "2001", [(a, BASE, False), (b, BASE + 120, False)], day="WEEKDAY")
    add_train(db, "2001", [(a, BASE, False), (b, BASE + 120, False)], day="SATURDAY")
    add_train(db, "5002", [(b, BASE, False), (a, BASE + 120, False)], day="HOLIDAY", direction=2)
    db.commit()

    pairs, _ = derive_adjacent_pairs(db)

    assert pairs == {pair(a, b): 2}


def test_loaded_edges_follow_the_same_rules(db):
    a, b, c = sl_id(db, 3), sl_id(db, 5), sl_id(db, 6)
    for i, train in enumerate(("2001", "2002")):
        t = BASE + i * 600
        add_train(db, train, [(a, t, False), (b, t + 120, True), (c, t + 240, False)])
    db.commit()

    report = load_derived_edges(db, dry_run=True)

    assert report.counts["adjacent_created"] == 0 and report.pairs == []
