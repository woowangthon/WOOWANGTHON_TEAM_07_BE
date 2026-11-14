"""OA-101 시간표 수집·적재. 이슈 #34, #35.

- 수집(네트워크)은 DB 쓰기 전에 모두 끝낸다. 하나라도 실패하면 아무것도 쓰지 않는다(기존 데이터 유지).
- 적재는 한 트랜잭션이다. (역-노선, 요일, 방향) 조합마다 기존 행을 지우고 새로 넣어 재실행해도 늘어나지 않는다.
- 역 코드는 OA-12035 외부 코드(station_external_ids)로 역-노선 행에 연결한다. 연결되지 않은 코드는 건너뛰고 센다.
- 응답의 LINE_NUM이 없거나 1~8호선이 아니거나 연결된 노선과 다른 행이 하나라도 있으면 전체 적재를 거부한다(#71).
  일부 행만 건너뛰면 그룹이 부분 스냅샷으로 바뀌기 때문이다. 기존 데이터는 그대로 남는다.
- fetched_at은 UTC 초 단위(...Z)로 정규화한다. 저장된 그룹보다 오래된 수집으로는 교체하지 않는다(같은 시각은 허용).
- EXPRESS_YN == "D"는 급행으로 추정한다(미검증). 인접역 추출에서 제외하는 데 쓴다.
"""

import re
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.clients.seoul_open_api import SeoulOpenApiClient
from app.models import StationExternalId, StationLine, TrainSchedule
from app.services.station_seed import SOURCE_OA12035
from app.services.timetable import (
    DAY_TYPE_TO_WEEK_TAG,
    DIRECTIONS,
    SERVICE_DAY_TYPES,
    TimetableError,
    line_num_to_line_id,
    time_or_none,
)
from app.timeutil import parse_utc_iso, to_utc_iso

SERVICE_NAME = "SearchSTNTimeTableByIDService"
EXPRESS_FLAG = "D"
EXPRESS_VALUES = ("G", "D")  # 확인된 값(docs/api-notes.md). 그 외 값은 급행 여부를 알 수 없어 거부한다.
# 열차 번호·시발/종착 역 코드. 영숫자 1~16자(DB 컬럼 길이). 확인된 예: K1902, 2001, 0150, 1728.
_IDENTIFIER_PATTERN = re.compile(r"[0-9A-Za-z]{1,16}")


@dataclass(frozen=True)
class StopRow:
    train_no: str
    arrival_time: int | None
    departure_time: int | None
    origin_station_code: str | None
    destination_station_code: str | None
    is_express: bool
    line_id: str | None


@dataclass
class TimetableReport:
    dry_run: bool = False
    counts: Counter = field(default_factory=Counter)


def normalize_fetched_at(value: Any) -> str:
    """fetched_at을 UTC 초 단위 저장 형식(...Z)으로 검증·정규화한다. +00:00은 Z로 바꾼다."""
    try:
        return to_utc_iso(parse_utc_iso(value))
    except ValueError:
        raise TimetableError(f"fetched_at은 UTC 초 단위 시각(예: 2026-11-14T03:00:00Z)이어야 합니다: {value!r}") from None


def _required_text(row: dict[str, Any], name: str, index: int) -> str:
    if name not in row:
        raise TimetableError(f"{index + 1}번째 행에 필드가 없습니다: {name}")
    value = row[name]
    if not isinstance(value, str):
        raise TimetableError(f"{index + 1}번째 행의 {name}가 문자열이 아닙니다: {type(value).__name__}")
    return value


def _optional_code(row: dict[str, Any], name: str, index: int) -> str | None:
    value = row.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TimetableError(f"{index + 1}번째 행의 {name}가 문자열이 아닙니다: {type(value).__name__}")
    value = value.strip()
    if not value:
        return None
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise TimetableError(f"{index + 1}번째 행의 {name} 형식이 올바르지 않습니다: {value!r}")
    return value


def parse_stop_rows(rows: list[dict[str, Any]], station_code: str) -> list[StopRow]:
    """응답 행을 StopRow로 바꾼다. 필수 필드가 없거나 형식·타입이 틀리거나 요청한 역 코드와 다르면 TimetableError다.

    LINE_NUM이 없거나 1~8호선이 아니면 line_id=None으로 두고, 적재(load_timetables)에서 그 그룹째 거부한다.
    """
    if not isinstance(rows, list):
        raise TimetableError(f"응답 행 목록이 배열이 아닙니다: {type(rows).__name__}")
    parsed: list[StopRow] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise TimetableError(f"{index + 1}번째 행이 객체가 아닙니다: {type(row).__name__}")
        code = _required_text(row, "STATION_CD", index)
        train_no = _required_text(row, "TRAIN_NO", index).strip()
        arrival = time_or_none(_required_text(row, "ARRIVETIME", index))
        departure = time_or_none(_required_text(row, "LEFTTIME", index))
        if code != station_code:
            raise TimetableError(f"{index + 1}번째 행의 STATION_CD({code!r})가 요청한 역 코드와 다릅니다.")
        if not train_no:
            raise TimetableError(f"{index + 1}번째 행의 TRAIN_NO가 비어 있습니다.")
        if not _IDENTIFIER_PATTERN.fullmatch(train_no):
            raise TimetableError(f"{index + 1}번째 행의 TRAIN_NO 형식이 올바르지 않습니다: {train_no!r}")
        if arrival is None and departure is None:
            raise TimetableError(f"{index + 1}번째 행에 도착·출발 시각이 모두 없습니다.")
        line_num = row.get("LINE_NUM")
        if line_num is not None and not isinstance(line_num, str):
            raise TimetableError(f"{index + 1}번째 행의 LINE_NUM이 문자열이 아닙니다: {type(line_num).__name__}")
        express = row.get("EXPRESS_YN")
        if express not in EXPRESS_VALUES:
            raise TimetableError(f"{index + 1}번째 행의 EXPRESS_YN 값이 G 또는 D가 아닙니다: {express!r}")
        parsed.append(
            StopRow(
                train_no=train_no,
                arrival_time=arrival,
                departure_time=departure,
                origin_station_code=_optional_code(row, "ORIGINSTATION", index),
                destination_station_code=_optional_code(row, "DESTSTATION", index),
                is_express=express == EXPRESS_FLAG,
                line_id=line_num_to_line_id(line_num or ""),
            )
        )
    return parsed


def merge_loop_terminal_rows(stops: list[StopRow]) -> tuple[list[StopRow], int]:
    """순환선 시발=종착역은 같은 열차가 출발 전용 행과 도착 전용 행으로 두 번 나온다. 한 행으로 합친다.

    합칠 수 있는 것은 같은 train_no에서 한쪽은 출발만, 다른 쪽은 도착만 있고 나머지 필드가 같은 두 행뿐이다.
    그 외 중복은 그대로 둬서 호출자가 오류로 처리한다. 합친 행은 시발 출발과 종착 도착을 함께 가진다.
    """
    result: list[StopRow] = []
    index_by_train: dict[str, int] = {}
    merged = 0
    for stop in stops:
        prev_index = index_by_train.get(stop.train_no)
        if prev_index is None:
            index_by_train[stop.train_no] = len(result)
            result.append(stop)
            continue
        prev = result[prev_index]
        departure_only_then_arrival_only = (
            prev.arrival_time is None and prev.departure_time is not None
            and stop.departure_time is None and stop.arrival_time is not None
        )
        arrival_only_then_departure_only = (
            prev.departure_time is None and prev.arrival_time is not None
            and stop.arrival_time is None and stop.departure_time is not None
        )
        same_trip = (
            prev.origin_station_code == stop.origin_station_code
            and prev.destination_station_code == stop.destination_station_code
            and prev.is_express == stop.is_express
            and prev.line_id == stop.line_id
        )
        if same_trip and (departure_only_then_arrival_only or arrival_only_then_departure_only):
            arrival = prev.arrival_time if prev.arrival_time is not None else stop.arrival_time
            departure = prev.departure_time if prev.departure_time is not None else stop.departure_time
            result[prev_index] = replace(prev, arrival_time=arrival, departure_time=departure)
            merged += 1
        else:
            result.append(stop)
    return result, merged


# (역 코드, 요일 종류, 방향) -> 정차 행
Collected = dict[tuple[str, str, int], list[StopRow]]


def fetch_timetables(
    client: SeoulOpenApiClient,
    station_codes: list[str],
    *,
    day_types: tuple[str, ...] = SERVICE_DAY_TYPES,
    directions: tuple[int, ...] = DIRECTIONS,
) -> Collected:
    """역 코드 x 요일 x 방향을 모두 받는다. 실패하면 예외를 올리고 부분 결과를 돌려주지 않는다."""
    collected: Collected = {}
    for code in station_codes:
        for day_type in day_types:
            for direction in directions:
                result = client.fetch_all_rows(
                    SERVICE_NAME, args=(code, DAY_TYPE_TO_WEEK_TAG[day_type], str(direction))
                )
                collected[(code, day_type, direction)] = parse_stop_rows(result.rows, code)
    return collected


def station_codes_in_db(session: Session) -> list[str]:
    """시간표를 받을 수 있는 역 코드(OA-12035 외부 코드 중 노선 연결이 있는 것)."""
    return sorted(
        session.scalars(
            select(StationExternalId.external_station_code).where(
                StationExternalId.source == SOURCE_OA12035,
                StationExternalId.station_line_id.is_not(None),
            )
        )
    )


def _stored_fetched_at(session: Session, station_line_id: int, day_type: str, direction: int) -> str | None:
    """그룹에 저장된 가장 최신 fetched_at. 비어 있으면 None."""
    values = session.scalars(
        select(TrainSchedule.fetched_at).distinct().where(
            TrainSchedule.station_line_id == station_line_id,
            TrainSchedule.service_day_type == day_type,
            TrainSchedule.direction == direction,
        )
    ).all()
    if not values:
        return None
    try:
        return max(to_utc_iso(parse_utc_iso(v)) for v in values)
    except ValueError:
        raise TimetableError(
            f"저장된 fetched_at 형식이 올바르지 않아 교체 여부를 판단할 수 없습니다: 역-노선 {station_line_id} {day_type} {direction}"
        ) from None


def load_timetables(
    session: Session, collected: Collected, *, fetched_at: str, dry_run: bool = False
) -> TimetableReport:
    """수집 결과를 적재한다. 실패하면 롤백하고 예외를 던진다. dry_run이면 적용해 본 뒤 롤백한다.

    쓰기 전에 모든 그룹의 노선을 검사한다. 노선 미확인·불일치 행이 있거나 저장된 데이터보다 오래된 수집이면
    TimetableError로 전체를 거부하고 기존 행과 fetched_at을 그대로 둔다.
    """
    report = TimetableReport(dry_run=dry_run)
    try:
        fetched_at = normalize_fetched_at(fetched_at)
        resolved = {
            code: (sl_id, line_id)
            for code, sl_id, line_id in session.execute(
                select(
                    StationExternalId.external_station_code,
                    StationExternalId.station_line_id,
                    StationLine.line_id,
                )
                .join(StationLine, StationLine.station_line_id == StationExternalId.station_line_id)
                .where(StationExternalId.source == SOURCE_OA12035)
            )
        }
        groups = sorted(collected.items())
        for (code, day_type, direction), stops in groups:
            found = resolved.get(code)
            if found is None:
                continue
            for stop in stops:
                if stop.line_id is None:
                    raise TimetableError(
                        f"노선을 확인할 수 없는 행이 있어 적재를 거부했습니다(LINE_NUM 없음·미지원): "
                        f"{code} {day_type} {direction} {stop.train_no}"
                    )
                if stop.line_id != found[1]:
                    raise TimetableError(
                        f"연결된 노선({found[1]}호선)과 다른 노선({stop.line_id}호선) 행이 있어 적재를 거부했습니다: "
                        f"{code} {day_type} {direction} {stop.train_no}"
                    )
        for (code, day_type, direction), stops in groups:
            found = resolved.get(code)
            if found is None:
                report.counts["unmapped_station_codes"] += 1
                continue
            station_line_id, _ = found
            stored = _stored_fetched_at(session, station_line_id, day_type, direction)
            if stored is not None and fetched_at < stored:
                raise TimetableError(
                    f"저장된 데이터({stored})보다 오래된 수집({fetched_at})으로는 교체하지 않습니다: {code} {day_type} {direction}"
                )
            session.execute(
                delete(TrainSchedule).where(
                    TrainSchedule.station_line_id == station_line_id,
                    TrainSchedule.service_day_type == day_type,
                    TrainSchedule.direction == direction,
                )
            )
            merged, merged_count = merge_loop_terminal_rows(stops)
            report.counts["loop_terminal_merged"] += merged_count
            seen: set[str] = set()
            for stop in merged:
                if stop.train_no in seen:
                    raise TimetableError(
                        f"같은 열차 번호가 중복되었습니다: {code} {day_type} {direction} {stop.train_no}"
                    )
                seen.add(stop.train_no)
                session.add(
                    TrainSchedule(
                        station_line_id=station_line_id,
                        service_day_type=day_type,
                        direction=direction,
                        train_no=stop.train_no,
                        arrival_time=stop.arrival_time,
                        departure_time=stop.departure_time,
                        origin_station_code=stop.origin_station_code,
                        destination_station_code=stop.destination_station_code,
                        is_express=stop.is_express,
                        fetched_at=fetched_at,
                    )
                )
                report.counts["rows_written"] += 1
            report.counts["station_groups"] += 1
            if not stops:
                report.counts["empty_groups"] += 1
        session.flush()
        if dry_run:
            session.rollback()
        else:
            session.commit()
    except Exception:
        session.rollback()
        raise
    return report
