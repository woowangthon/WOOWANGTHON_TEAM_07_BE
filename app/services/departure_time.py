"""출발 권장 시각 계산 (순수 함수). 이슈 #38, #39 / docs/api-contract.md 8.2절(초안).

도착 희망 시각에서 필요한 시간을 거꾸로 빼서 권장 출발 시각을 구한다.

    권장 출발 = 도착 희망 - (집→역 도보 + 역 진입 + 여유 시간 + 예상 이동 시간 + 출구 이동)

- DB, 네트워크, 파일, 시스템 시계에 접근하지 않는다. 현재 시각(now)은 선택 인자로 주입한다.
- 경로 탐색을 하지 않는다. 예상 이동 시간은 외부에서 받은 값이다.
- 실제 열차 시간표를 쓰지 않는다. 그래서 결과는 항상 estimated=True, timetable_used=False이고
  limitations에 데이터 한계를 담는다. 탑승 가능한 열차를 고르지 않는다.
- 시각은 aware datetime만 받는다(naive 거부). 내부 계산은 UTC로 하고, 응답용 변환(to_kst_dict)은
  KST(+09:00) 문자열로 만든다. 날짜가 바뀌어도(자정 이후) 같은 규칙으로 계산한다.
- 결과를 저장하지 않는다.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.departure_defaults import (
    BUFFER_MINUTES_MAX,
    BUFFER_MINUTES_MIN,
    EXIT_WALK_DEFAULT,
    SEGMENT_MINUTES_MAX,
    SEGMENT_MINUTES_MIN,
    STATION_ENTRY_DEFAULT,
    TRAVEL_MINUTES_MAX,
    TRAVEL_MINUTES_MIN,
    WALK_TO_STATION_DEFAULT,
)
from app.services.status_messages import (
    DEP_ALREADY_PAST,
    DEP_NO_TIMETABLE,
    DEP_SEGMENT_ESTIMATES,
    DEP_TRAVEL_EXTERNAL,
)
from app.timeutil import KST, UTC, to_kst_iso

_ISO_WITH_OFFSET = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})")


@dataclass(frozen=True)
class TimelineStep:
    kind: str  # WALK_TO_STATION / STATION_ENTRY / BUFFER / RIDE / EXIT_WALK
    label: str
    start_at: datetime  # UTC
    end_at: datetime  # UTC
    minutes: int


@dataclass(frozen=True)
class DepartureInputs:
    travel_minutes: int
    buffer_minutes: int
    walk_to_station_minutes: int
    station_entry_minutes: int
    exit_walk_minutes: int


@dataclass(frozen=True)
class DepartureRecommendation:
    arrival_at: datetime  # UTC
    recommended_departure_at: datetime  # UTC
    total_minutes: int
    timeline: tuple[TimelineStep, ...]
    inputs: DepartureInputs
    date_changed: bool  # KST 날짜 기준으로 출발일과 도착일이 다르다(자정 이전 출발)
    already_past: bool | None  # now를 주지 않으면 None
    estimated: bool
    timetable_used: bool
    limitations: tuple[str, ...]


def _minutes(name: str, value: object, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name}은(는) 정수(분)여야 합니다.")
    if not low <= value <= high:
        raise ValueError(f"{name}은(는) {low}~{high}분 사이여야 합니다.")
    return value


def _aware(name: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name}은(는) datetime이어야 합니다.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name}에는 타임존 정보가 필요합니다.")
    if value.microsecond != 0:
        raise ValueError(f"{name}은(는) 초 단위까지만 허용합니다.")
    return value.astimezone(UTC)


def parse_arrival_iso(value: object) -> datetime:
    """오프셋이 있는 ISO 8601 문자열(예: 2026-11-13T09:00:00+09:00, ...Z)을 aware datetime으로 바꾼다.

    날짜만 있거나 오프셋이 없는 문자열, 형식이 틀린 값은 ValueError다.
    """
    if not isinstance(value, str) or not _ISO_WITH_OFFSET.fullmatch(value):
        raise ValueError("도착 희망 시각은 오프셋이 있는 ISO 8601(초 단위) 문자열이어야 합니다.")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("도착 희망 시각의 날짜 또는 시각 값이 올바르지 않습니다.") from None


def recommend_departure(
    arrival_at: datetime,
    *,
    travel_minutes: int,
    buffer_minutes: int,
    walk_to_station_minutes: int = WALK_TO_STATION_DEFAULT,
    station_entry_minutes: int = STATION_ENTRY_DEFAULT,
    exit_walk_minutes: int = EXIT_WALK_DEFAULT,
    now: datetime | None = None,
) -> DepartureRecommendation:
    """권장 출발 시각과 타임라인을 계산한다. 잘못된 입력은 ValueError다.

    - arrival_at: 도착 희망 시각(aware).
    - travel_minutes: 외부에서 받은 예상 이동 시간(1~720분).
    - buffer_minutes: 필요한 여유 시간(0~240분). 탑승 전에 둔다.
    - walk_to_station_minutes, station_entry_minutes, exit_walk_minutes: 추정값(0~120분).
    - now: 주면 권장 출발 시각이 이미 지났는지 표시한다(aware).
    """
    arrival = _aware("도착 희망 시각", arrival_at)
    inputs = DepartureInputs(
        travel_minutes=_minutes("예상 이동 시간", travel_minutes, TRAVEL_MINUTES_MIN, TRAVEL_MINUTES_MAX),
        buffer_minutes=_minutes("여유 시간", buffer_minutes, BUFFER_MINUTES_MIN, BUFFER_MINUTES_MAX),
        walk_to_station_minutes=_minutes("집→역 도보 시간", walk_to_station_minutes, SEGMENT_MINUTES_MIN, SEGMENT_MINUTES_MAX),
        station_entry_minutes=_minutes("역 진입 시간", station_entry_minutes, SEGMENT_MINUTES_MIN, SEGMENT_MINUTES_MAX),
        exit_walk_minutes=_minutes("출구 이동 시간", exit_walk_minutes, SEGMENT_MINUTES_MIN, SEGMENT_MINUTES_MAX),
    )
    current = _aware("현재 시각", now) if now is not None else None

    plan = (
        ("WALK_TO_STATION", "집에서 역까지 도보", inputs.walk_to_station_minutes),
        ("STATION_ENTRY", "역 진입", inputs.station_entry_minutes),
        ("BUFFER", "여유 시간", inputs.buffer_minutes),
        ("RIDE", "열차 이동(외부 입력 예상 시간)", inputs.travel_minutes),
        ("EXIT_WALK", "출구 이동", inputs.exit_walk_minutes),
    )
    total = sum(minutes for _, _, minutes in plan)
    departure = arrival - timedelta(minutes=total)

    steps: list[TimelineStep] = []
    cursor = departure
    for kind, label, minutes in plan:
        end = cursor + timedelta(minutes=minutes)
        steps.append(TimelineStep(kind=kind, label=label, start_at=cursor, end_at=end, minutes=minutes))
        cursor = end

    already_past = None if current is None else departure < current
    limitations = [DEP_NO_TIMETABLE, DEP_TRAVEL_EXTERNAL, DEP_SEGMENT_ESTIMATES]
    if already_past:
        limitations.append(DEP_ALREADY_PAST)

    return DepartureRecommendation(
        arrival_at=arrival,
        recommended_departure_at=departure,
        total_minutes=total,
        timeline=tuple(steps),
        inputs=inputs,
        date_changed=departure.astimezone(KST).date() != arrival.astimezone(KST).date(),
        already_past=already_past,
        estimated=True,
        timetable_used=False,
        limitations=tuple(limitations),
    )


def to_kst_dict(rec: DepartureRecommendation) -> dict:
    """응답용 사전. 모든 시각은 KST(+09:00) ISO 8601 문자열이다(계약 0.2절). 계약은 초안이다."""
    return {
        "recommended_departure_at": to_kst_iso(rec.recommended_departure_at),
        "arrival_at": to_kst_iso(rec.arrival_at),
        "total_minutes": rec.total_minutes,
        "date_changed": rec.date_changed,
        "already_past": rec.already_past,
        "estimated": rec.estimated,
        "timetable_used": rec.timetable_used,
        "inputs": {
            "travel_minutes": rec.inputs.travel_minutes,
            "buffer_minutes": rec.inputs.buffer_minutes,
            "walk_to_station_minutes": rec.inputs.walk_to_station_minutes,
            "station_entry_minutes": rec.inputs.station_entry_minutes,
            "exit_walk_minutes": rec.inputs.exit_walk_minutes,
        },
        "timeline": [
            {
                "kind": s.kind,
                "label": s.label,
                "start_at": to_kst_iso(s.start_at),
                "end_at": to_kst_iso(s.end_at),
                "minutes": s.minutes,
            }
            for s in rec.timeline
        ],
        "limitations": list(rec.limitations),
    }
