"""scripts/build_station_seed.py의 환승 확정 규칙 검증 (합성 데이터 사용).

역명이 같다는 이유만으로는 확정되지 않고, 수동 확정은 근거 검증에 실패하면 중단되는지 확인한다.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_station_seed", ROOT / "scripts" / "build_station_seed.py")
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)


def seed_row(code, line, name, addr=""):
    return {"code": code, "line_id": line, "name": name, "full_addr": addr, "addr": addr}


def official(line, name, to, dist="100", tm="01:00"):
    return {"호선": line, "환승역명": name, "환승노선": f"{to}호선", "환승거리": dist, "환승소요시간": tm}


def test_same_name_without_official_record_is_not_confirmed():
    seed = [seed_row("0001", "1", "가나"), seed_row("0002", "2", "가나")]

    groups, evidence, _ = build.verify_transfers(seed, [])

    assert groups == [] and evidence == []


def test_official_record_in_both_directions_confirms_group():
    seed = [seed_row("0001", "1", "가나"), seed_row("0002", "2", "가나")]
    rows = [official("1", "가나", "2"), official("2", "가나", "1")]

    groups, evidence, _ = build.verify_transfers(seed, rows)

    assert groups == [(("0001", "0002"), ("1", "2"))]
    assert len(evidence) == 2


def test_one_direction_only_is_not_confirmed():
    seed = [seed_row("0001", "1", "가나"), seed_row("0002", "2", "가나")]

    groups, _, unresolved = build.verify_transfers(seed, [official("1", "가나", "2")])

    assert groups == [] and unresolved


def test_mismatched_distance_is_not_confirmed():
    seed = [seed_row("0001", "1", "가나"), seed_row("0002", "2", "가나")]
    rows = [official("1", "가나", "2", dist="100"), official("2", "가나", "1", dist="250")]

    groups, _, _ = build.verify_transfers(seed, rows)

    assert groups == []


def test_incomplete_three_line_group_is_not_confirmed():
    seed = [seed_row("0001", "1", "가나"), seed_row("0002", "2", "가나"), seed_row("0003", "3", "가나")]
    # 1↔2, 2↔3만 있고 1↔3이 없다.
    rows = [
        official("1", "가나", "2"), official("2", "가나", "1"),
        official("2", "가나", "3"), official("3", "가나", "2"),
    ]

    groups, _, _ = build.verify_transfers(seed, rows)

    assert groups == []


def test_ambiguous_name_on_same_line_is_not_resolved():
    seed = [seed_row("0001", "1", "가나(동)"), seed_row("0003", "1", "가나(서)"), seed_row("0002", "2", "가나")]
    rows = [official("1", "가나", "2"), official("2", "가나", "1")]

    groups, _, _ = build.verify_transfers(seed, rows)

    assert groups == []  # 1호선에 괄호 부연을 뺀 이름이 같은 역이 둘이라 유일하지 않다


def manual_fixture():
    by_code = {
        "0001": seed_row("0001", "1", "서", "주소 1 서울역(1호선)"),
        "0002": seed_row("0002", "4", "서", "주소 2 서울역 (4호선)"),
    }
    rows = [official("1", "서울역", "4", "159", "02:13"), official("4", "서울역", "1", "159", "02:13")]
    return by_code, rows


def test_manual_group_requires_verified_basis():
    by_code, rows = manual_fixture()

    evidence, facts = build.verify_manual_group(("0001", "0002"), "서울역", by_code, rows)

    assert {e[4] for e in evidence} == {"manual"} and "159m" in facts


@pytest.mark.parametrize("break_what", ["reverse_record", "distance", "address"])
def test_manual_group_fails_when_basis_is_missing(break_what):
    by_code, rows = manual_fixture()
    if break_what == "reverse_record":
        rows = rows[:1]
    elif break_what == "distance":
        rows[1]["환승거리"] = "300"
    else:
        by_code["0002"]["full_addr"] = "주소 2 다른역 (4호선)"

    with pytest.raises(ValueError):
        build.verify_manual_group(("0001", "0002"), "서울역", by_code, rows)


def test_manual_confirmations_list_is_exactly_seoul_station():
    assert build.MANUAL_CONFIRMATIONS == {("0150", "0426"): "서울역"}
