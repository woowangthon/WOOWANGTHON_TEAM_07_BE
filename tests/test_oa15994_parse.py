"""OA-15994 파서 검증. 저장된 샘플(docs/samples)을 픽스처로 쓰고 네트워크·DB는 쓰지 않는다."""

import json
from collections import Counter
from dataclasses import fields
from pathlib import Path

import pytest

from app.clients.oa15994 import (
    REQUIRED_FIELDS,
    ElevatorRecord,
    Oa15994ParseError,
    load_sample_rows,
    parse_elevators,
)

SAMPLE = Path(__file__).resolve().parent.parent / "docs" / "samples" / "OA-15994_SeoulMetroFaciInfo_2026-11-13.json"


@pytest.fixture(scope="module")
def sample_rows() -> list[dict]:
    return load_sample_rows(json.loads(SAMPLE.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def parsed(sample_rows):
    return parse_elevators(sample_rows)


def row(**overrides) -> dict:
    base = {
        "STN_CD": "0150",
        "STN_NM": "서울역(1)",
        "ELVTR_NM": "승강기)엘리베이터-서울(1)역 2번 출구측 외부#1",
        "OPR_SEC": "B1-1F",
        "INSTL_PSTN": "2번 출입구",
        "USE_YN": "사용가능",
        "ELVTR_SE": "EV",
    }
    base.update(overrides)
    return base


# ---------- 저장된 샘플 ----------


def test_sample_document_structure(sample_rows):
    assert len(sample_rows) == 2809
    assert set(sample_rows[0]) == set(REQUIRED_FIELDS)  # 확인된 7개 필드뿐이다


def test_sample_yields_866_elevators_in_273_stations(parsed):
    assert len(parsed.elevators) == 866
    assert len(parsed.station_codes) == 273
    assert parsed.total_rows == 2809


def test_non_elevator_types_are_excluded(parsed):
    assert parsed.excluded_by_type == Counter({"ES": 1884, "WL": 41, "MW": 18})
    assert all(e.station_code for e in parsed.elevators)
    assert len(parsed.elevators) + sum(parsed.excluded_by_type.values()) == parsed.total_rows


def test_sample_status_counts(parsed):
    counts = Counter(e.status for e in parsed.elevators)
    # docs/api-notes.md 5절: EV 기준 사용가능 845 / 보수중 21
    assert counts == Counter({"가동중": 845, "가동 안됨": 21})
    assert parsed.unknown_status == []


def test_sample_identifiers_are_unique(parsed):
    keys = [e.key for e in parsed.elevators]
    assert len(keys) == len(set(keys))


def test_sample_station_codes_are_four_digits(parsed):
    assert all(len(c) == 4 and c.isdigit() for c in parsed.station_codes)


def test_record_has_no_invented_fields():
    names = {f.name for f in fields(ElevatorRecord)}
    assert names == {
        "station_code", "station_name", "elevator_name", "operation_section",
        "location_description", "source_status_text", "status",
    }
    assert "observed_at" not in names and "fetched_at" not in names  # 원본에 없는 시각은 만들지 않는다


def test_input_order_is_preserved(sample_rows, parsed):
    expected = [r["ELVTR_NM"] for r in sample_rows if r["ELVTR_SE"] == "EV"]
    assert [e.elevator_name for e in parsed.elevators] == expected


# ---------- 필드 매핑 ----------


def test_row_is_mapped_field_by_field():
    result = parse_elevators([row()])

    e = result.elevators[0]
    assert e == ElevatorRecord(
        station_code="0150",
        station_name="서울역(1)",
        elevator_name="승강기)엘리베이터-서울(1)역 2번 출구측 외부#1",
        operation_section="B1-1F",
        location_description="2번 출입구",
        source_status_text="사용가능",
        status="가동중",
    )


def test_empty_optional_fields_become_none():
    e = parse_elevators([row(OPR_SEC="", INSTL_PSTN="  ")]).elevators[0]

    assert e.operation_section is None
    assert e.location_description is None


def test_names_are_not_altered():
    name = "  승강기)엘리베이터 이름  "  # 식별자에 쓰이므로 공백을 포함해 그대로 둔다
    assert parse_elevators([row(ELVTR_NM=name)]).elevators[0].elevator_name == name


def test_extra_fields_are_ignored_not_used():
    e = parse_elevators([row(UNKNOWN_FIELD="x")]).elevators[0]

    assert not hasattr(e, "UNKNOWN_FIELD")


# ---------- 상태 매핑 ----------


@pytest.mark.parametrize(
    "use_yn, status",
    [("사용가능", "가동중"), ("보수중", "가동 안됨")],
)
def test_confirmed_status_values_are_mapped_explicitly(use_yn, status):
    e = parse_elevators([row(USE_YN=use_yn)]).elevators[0]

    assert e.status == status
    assert e.source_status_text == use_yn


@pytest.mark.parametrize("use_yn", ["고장", "점검", "사용 가능", " 사용가능", "사용가능 ", "OK", "정상"])
def test_unrecognized_status_is_kept_unknown_and_never_guessed(use_yn):
    result = parse_elevators([row(USE_YN=use_yn)])

    e = result.elevators[0]
    assert e.status is None  # 가동중으로도 가동 안됨으로도 판단하지 않는다
    assert e.source_status_text == use_yn  # 원문은 보존한다
    assert result.unknown_status == [e]


# ---------- 형식 검증 ----------


@pytest.mark.parametrize("missing", REQUIRED_FIELDS)
def test_missing_field_is_rejected(missing):
    broken = row()
    del broken[missing]

    with pytest.raises(Oa15994ParseError, match=missing):
        parse_elevators([broken])


@pytest.mark.parametrize("empty_field", ["STN_CD", "STN_NM", "ELVTR_NM", "USE_YN", "ELVTR_SE"])
@pytest.mark.parametrize("blank", ["", "   "])
def test_empty_required_value_is_rejected(empty_field, blank):
    with pytest.raises(Oa15994ParseError, match=empty_field):
        parse_elevators([row(**{empty_field: blank})])


@pytest.mark.parametrize("code", ["150", "01500", "015A", "", "０１５０"])
def test_invalid_station_code_is_rejected(code):
    with pytest.raises(Oa15994ParseError):
        parse_elevators([row(STN_CD=code)])


@pytest.mark.parametrize("bad", [None, 150, ["EV"]])
def test_non_string_value_is_rejected(bad):
    with pytest.raises(Oa15994ParseError, match="문자열"):
        parse_elevators([row(ELVTR_NM=bad)])


def test_non_object_row_is_rejected():
    with pytest.raises(Oa15994ParseError, match="객체"):
        parse_elevators([row(), "not-a-row"])


def test_rows_must_be_a_list():
    with pytest.raises(Oa15994ParseError, match="배열"):
        parse_elevators({"row": []})


def test_one_bad_row_rejects_the_whole_batch():
    with pytest.raises(Oa15994ParseError, match=r"row\[1\]"):
        parse_elevators([row(), row(ELVTR_NM="B", STN_CD="bad"), row(ELVTR_NM="C")])


def test_error_message_names_row_and_field_but_not_values():
    with pytest.raises(Oa15994ParseError) as info:
        parse_elevators([row(ELVTR_NM="비밀설비명", USE_YN="")])

    assert "row[0]" in str(info.value) and "USE_YN" in str(info.value)
    assert "비밀설비명" not in str(info.value)


# ---------- 식별자 중복 ----------


def test_duplicate_identifier_in_same_station_is_rejected():
    with pytest.raises(Oa15994ParseError, match="중복"):
        parse_elevators([row(), row(USE_YN="보수중")])


def test_same_elevator_name_in_different_stations_is_allowed():
    result = parse_elevators([row(), row(STN_CD="0151")])

    assert len(result.elevators) == 2


def test_duplicates_among_excluded_types_do_not_matter():
    escalator = row(ELVTR_SE="ES")
    result = parse_elevators([escalator, dict(escalator)])

    assert result.elevators == []
    assert result.excluded_by_type == Counter({"ES": 2})


def test_unknown_elevator_types_are_excluded_not_guessed():
    result = parse_elevators([row(ELVTR_SE="XX"), row(ELVTR_NM="B")])

    assert [e.elevator_name for e in result.elevators] == ["B"]
    assert result.excluded_by_type == Counter({"XX": 1})


# ---------- 샘플 문서 로더 ----------


def test_sample_loader_rejects_wrong_service():
    with pytest.raises(Oa15994ParseError, match="service"):
        load_sample_rows({"service": "Other", "list_total_count": 0, "row": []})


def test_sample_loader_rejects_count_mismatch():
    with pytest.raises(Oa15994ParseError, match="list_total_count"):
        load_sample_rows({"service": "SeoulMetroFaciInfo", "list_total_count": 2, "row": [row()]})


def test_sample_loader_does_not_invent_result():
    document = {"service": "SeoulMetroFaciInfo", "list_total_count": 1, "row": [row()]}

    assert load_sample_rows(document) == [row()]
    assert "RESULT" not in document
