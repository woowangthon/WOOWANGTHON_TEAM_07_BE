"""열차 시간표(OA-101) 변환과 조회. 이슈 #34, #35.

- 시각은 서비스일 0시 기준 정수 초다. 24시를 넘는 값을 그대로 받는다(24:30:00 -> 88200).
- 원본의 00:00:00은 "없음"이다(시발역의 도착, 종착역의 출발). 정수 0이 아니라 None으로 바꾼다.
- WEEK_TAG 1, 2, 3은 평일, 토요일, 일요일·공휴일로 추정한다(공식 표 미확인, docs/api-notes.md).
- 계획 시각이다. 지연은 반영하지 않는다.
"""

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TrainSchedule

SERVICE_DAY_TYPES = ("WEEKDAY", "SATURDAY", "HOLIDAY")
WEEK_TAG_TO_DAY_TYPE = {"1": "WEEKDAY", "2": "SATURDAY", "3": "HOLIDAY"}
DAY_TYPE_TO_WEEK_TAG = {v: k for k, v in WEEK_TAG_TO_DAY_TYPE.items()}
DIRECTIONS = (1, 2)

SECONDS_PER_DAY = 86400
MAX_TIME_SECONDS = 48 * 3600 - 1  # 서비스일 기준 47:59:59까지만 받는다

_TIME_PATTERN = re.compile(r"(\d{1,2}):([0-5]\d):([0-5]\d)")


class TimetableError(ValueError):
    """시간표 값이 규칙에 맞지 않을 때."""


def time_to_seconds(value: str) -> int:
    """"HH:MM:SS"를 서비스일 0시 기준 초로 바꾼다. 24시 이상도 허용한다. 24:30:00 -> 88200."""
    if not isinstance(value, str):
        raise TimetableError(f"시각이 문자열이 아닙니다: {value!r}")
    match = _TIME_PATTERN.fullmatch(value.strip())
    if match is None:
        raise TimetableError(f"시각 형식이 올바르지 않습니다: {value!r}")
    hours, minutes, seconds = (int(g) for g in match.groups())
    total = hours * 3600 + minutes * 60 + seconds
    if total > MAX_TIME_SECONDS:
        raise TimetableError(f"서비스일 범위(47:59:59)를 벗어났습니다: {value!r}")
    return total


def time_or_none(value: str) -> int | None:
    """원본의 00:00:00(없음)은 None으로 바꾼다."""
    seconds = time_to_seconds(value)
    return None if seconds == 0 else seconds


def seconds_to_time(seconds: int) -> str:
    """초를 "HH:MM:SS"로 되돌린다. 24시 이상이면 24:30:00처럼 쓴다."""
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 0 <= seconds <= MAX_TIME_SECONDS:
        raise TimetableError(f"초 값이 범위를 벗어났습니다: {seconds!r}")
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def week_tag_to_day_type(week_tag: str) -> str:
    try:
        return WEEK_TAG_TO_DAY_TYPE[str(week_tag)]
    except KeyError:
        raise TimetableError(f"알 수 없는 WEEK_TAG: {week_tag!r}") from None


def day_type_for_date(day: date, holidays: frozenset[date] = frozenset()) -> str:
    """날짜의 서비스일 종류. 공휴일 목록은 호출자가 준다(공휴일 데이터는 이 서비스에 없다)."""
    if day in holidays or day.weekday() == 6:
        return "HOLIDAY"
    if day.weekday() == 5:
        return "SATURDAY"
    return "WEEKDAY"


def line_num_to_line_id(line_num: str) -> str | None:
    """"01호선" -> "1". 1~8호선이 아니면 None."""
    match = re.fullmatch(r"0?([1-8])호선", (line_num or "").strip())
    return match.group(1) if match else None


def find_last_train_departing_by(
    session: Session,
    *,
    station_line_id: int,
    day_type: str,
    direction: int,
    latest_departure_seconds: int,
) -> TrainSchedule | None:
    """지정한 시각 이전(같은 시각 포함)에 출발하는 가장 늦은 열차. 없으면 None.

    출발 시각이 없는 행(종착)은 제외한다. 같은 출발 시각이면 train_no가 큰 쪽이 아니라 먼저 입력된 쪽을 돌려준다.
    """
    if day_type not in SERVICE_DAY_TYPES or direction not in DIRECTIONS:
        raise TimetableError("day_type 또는 direction이 올바르지 않습니다.")
    stmt = (
        select(TrainSchedule)
        .where(
            TrainSchedule.station_line_id == station_line_id,
            TrainSchedule.service_day_type == day_type,
            TrainSchedule.direction == direction,
            TrainSchedule.departure_time.is_not(None),
            TrainSchedule.departure_time <= latest_departure_seconds,
        )
        .order_by(TrainSchedule.departure_time.desc(), TrainSchedule.schedule_id)
        .limit(1)
    )
    return session.scalars(stmt).first()
