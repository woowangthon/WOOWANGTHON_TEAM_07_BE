"""엘리베이터·역 상태 판정 (순수 함수). docs/api-contract.md 6.1절, 6.2절을 따른다.

이 모듈은 입력만 보고 결과를 돌려준다.
- DB, 네트워크, 파일에 접근하지 않는다.
- 시스템 시계를 읽지 않는다. 현재 시각(now)은 호출자가 UTC로 주입한다.
- 결과를 저장하지 않는다. KST 변환은 API 계층(#28)에서 한다. 여기서는 UTC 비교만 한다.

시각(중요)
- fetched_at은 우리가 응답을 가져오거나 적재한 시각(수집·적재 시각)이다.
  원본 데이터에서 엘리베이터 상태가 관측된 시각이 아니다. OA-15994에는 관측 시각이 없어
  observed_at은 항상 NULL이며, 신선도 판정은 fetched_at만으로 한다.
- 신선도: 나이 = now − fetched_at. 나이 ≤ TTL이면 신선하다(경계값은 신선).
  fetched_at이 now보다 미래이면 시계 오차를 허용하지 않고 오류다(미래 데이터를 신선하다고 보지 않는다).

TTL 기본값 30분은 임시 기본값이다(D1, 갱신 주기 미검증). 설정(ELEVATOR_STATUS_TTL_MINUTES)으로 1~1440분 사이에서 바꾼다.

#24 정책과의 관계
- 미확인 상태는 기존 상태 행을 갱신하지 않으므로, 오래된 행은 시간이 지나 STALE이 된다.
  이 모듈은 저장된 행을 그대로 판정하며, 행을 보정하거나 추정하지 않는다.

설비 판정
- 상태 행 없음          -> status None, data_fresh False, unknown_reason NO_DATA
- 나이 > TTL            -> status None, data_fresh False, unknown_reason STALE
- 나이 ≤ TTL            -> 저장된 가동중 또는 가동 안됨, data_fresh True, unknown_reason None

역 판정 (확인된 설비만으로 판정. STALE과 NO_DATA는 모두 미확인으로 센다)
- T = 역의 엘리베이터 수, U = 미확인(NO_DATA + STALE) 수, C = T − U
- T = 0          -> status None, data_fresh False, unknown_elevator_count 0, 문구 "엘리베이터 정보가 없어..."
- T > 0, C = 0   -> status None, data_fresh False, unknown_elevator_count T, 문구 "확인 가능한 엘리베이터 상태가 없어..."
- C = T          -> 전부 가동중이면 AVAILABLE, 전부 가동 안됨이면 UNAVAILABLE, 섞이면 PARTIAL. data_fresh True, 문구 없음
- 0 < C < T      -> 확인된 설비로 AVAILABLE/PARTIAL/UNAVAILABLE 판정. data_fresh False, unknown_elevator_count U,
                    문구 "일부 설비 상태가 확인되지 않아..."

이 함수는 1호선 문구를 붙이지 않는다. 노선 정보는 역 검색·상세 API(#21, #22)에서 붙인다.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

from app.ttl_limits import TTL_MINUTES_MAX, TTL_MINUTES_MIN
from app.services.status_messages import NO_CONFIRMED, NO_ELEVATORS, PARTIAL_UNKNOWN

STATUS_RUNNING = "가동중"
STATUS_STOPPED = "가동 안됨"
ALLOWED_STATUSES = frozenset({STATUS_RUNNING, STATUS_STOPPED})

REASON_NO_DATA = "NO_DATA"
REASON_STALE = "STALE"

STATION_AVAILABLE = "AVAILABLE"
STATION_PARTIAL = "PARTIAL"
STATION_UNAVAILABLE = "UNAVAILABLE"

_UTC_STRING = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_UTC = timezone.utc


@dataclass(frozen=True)
class ElevatorRef:
    """판정 대상 설비. station_id는 설비가 속한 역이다."""

    elevator_id: int
    station_id: int


@dataclass(frozen=True)
class StatusRow:
    """저장된 최신 상태 한 행. fetched_at은 'YYYY-MM-DDTHH:MM:SSZ' UTC 문자열이다."""

    elevator_id: int
    status: str
    fetched_at: str


@dataclass(frozen=True)
class ElevatorJudgment:
    """설비 판정 결과. observed_at은 OA-15994에 원본 관측 시각이 없어 항상 None이다."""

    elevator_id: int
    status: str | None
    data_fresh: bool
    unknown_reason: str | None  # None(확인됨) / "NO_DATA" / "STALE"
    fetched_at: str | None  # 저장된 수집·적재 시각(UTC 문자열). 행이 없으면 None
    observed_at: None = None


@dataclass(frozen=True)
class StationJudgment:
    """역 판정 결과. limitations는 계약의 표준 문구만 담는다(없으면 빈 튜플)."""

    station_id: int
    status: str | None  # AVAILABLE / PARTIAL / UNAVAILABLE / None
    data_fresh: bool
    total_elevator_count: int
    unknown_elevator_count: int
    limitations: tuple[str, ...]
    elevators: tuple[ElevatorJudgment, ...]


def _validate_ttl(ttl_minutes: object) -> int:
    if isinstance(ttl_minutes, bool) or not isinstance(ttl_minutes, int):
        raise ValueError("TTL은 정수(분)여야 합니다.")
    if not TTL_MINUTES_MIN <= ttl_minutes <= TTL_MINUTES_MAX:
        raise ValueError(f"TTL은 {TTL_MINUTES_MIN}~{TTL_MINUTES_MAX}분 사이여야 합니다.")
    return ttl_minutes


def _validate_now(now: object) -> datetime:
    if not isinstance(now, datetime):
        raise ValueError("현재 시각(now)은 datetime이어야 합니다.")
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("현재 시각(now)은 UTC 기준의 timezone-aware datetime이어야 합니다.")
    return now.astimezone(_UTC)


def parse_stored_utc(value: object) -> datetime:
    """저장된 fetched_at을 읽는다. 'YYYY-MM-DDTHH:MM:SSZ' 형식만 허용한다(오프셋 표기 거부)."""
    if not isinstance(value, str) or not _UTC_STRING.fullmatch(value):
        raise ValueError("fetched_at은 YYYY-MM-DDTHH:MM:SSZ 형식의 UTC 문자열이어야 합니다.")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ValueError("fetched_at의 날짜 또는 시각 값이 올바르지 않습니다.") from None
    return parsed.replace(tzinfo=_UTC)


def _judge_elevator(
    elevator: ElevatorRef,
    row: StatusRow | None,
    now: datetime,
    ttl: timedelta,
) -> ElevatorJudgment:
    if row is None:
        return ElevatorJudgment(
            elevator_id=elevator.elevator_id, status=None, data_fresh=False,
            unknown_reason=REASON_NO_DATA, fetched_at=None,
        )
    stored = parse_stored_utc(row.fetched_at)
    if stored > now:
        raise ValueError(
            f"설비 {elevator.elevator_id}의 fetched_at이 현재 시각보다 미래입니다. 시계 오차를 허용하지 않습니다."
        )
    if row.status not in ALLOWED_STATUSES:
        raise ValueError(f"설비 {elevator.elevator_id}의 상태 값이 허용되지 않습니다.")
    if now - stored > ttl:
        return ElevatorJudgment(
            elevator_id=elevator.elevator_id, status=None, data_fresh=False,
            unknown_reason=REASON_STALE, fetched_at=row.fetched_at,
        )
    return ElevatorJudgment(
        elevator_id=elevator.elevator_id, status=row.status, data_fresh=True,
        unknown_reason=None, fetched_at=row.fetched_at,
    )


def _judge_station(station_id: int, judged: Sequence[ElevatorJudgment]) -> StationJudgment:
    total = len(judged)
    if total == 0:
        return StationJudgment(
            station_id=station_id, status=None, data_fresh=False,
            total_elevator_count=0, unknown_elevator_count=0,
            limitations=(NO_ELEVATORS,), elevators=(),
        )

    confirmed = [e for e in judged if e.unknown_reason is None]
    unknown = total - len(confirmed)  # NO_DATA와 STALE을 모두 미확인으로 센다
    if not confirmed:
        return StationJudgment(
            station_id=station_id, status=None, data_fresh=False,
            total_elevator_count=total, unknown_elevator_count=total,
            limitations=(NO_CONFIRMED,), elevators=tuple(judged),
        )

    statuses = {e.status for e in confirmed}
    if statuses == {STATUS_RUNNING}:
        status = STATION_AVAILABLE
    elif statuses == {STATUS_STOPPED}:
        status = STATION_UNAVAILABLE
    else:
        status = STATION_PARTIAL

    if unknown == 0:
        return StationJudgment(
            station_id=station_id, status=status, data_fresh=True,
            total_elevator_count=total, unknown_elevator_count=0,
            limitations=(), elevators=tuple(judged),
        )
    return StationJudgment(
        station_id=station_id, status=status, data_fresh=False,
        total_elevator_count=total, unknown_elevator_count=unknown,
        limitations=(PARTIAL_UNKNOWN,), elevators=tuple(judged),
    )


def judge_stations(
    elevators: Iterable[ElevatorRef],
    statuses: Iterable[StatusRow],
    *,
    now: datetime,
    ttl_minutes: int,
) -> list[StationJudgment]:
    """설비와 상태 행을 역별로 판정한다. 결과는 station_id 오름차순이다.

    - 같은 elevator_id가 둘 이상이면 오류다.
    - 상태 행이 설비 목록에 없으면 무시한다(호출자가 역 일부만 넘긴 경우).
    - 잘못된 now, TTL, fetched_at, 상태 값, 미래 fetched_at은 모두 ValueError다.
    """
    now_utc = _validate_now(now)
    ttl = timedelta(minutes=_validate_ttl(ttl_minutes))

    device_list = list(elevators)
    seen: set[int] = set()
    for device in device_list:
        if not isinstance(device, ElevatorRef):
            raise ValueError("설비 항목은 ElevatorRef여야 합니다.")
        if device.elevator_id in seen:
            raise ValueError(f"설비 식별자가 중복되었습니다: {device.elevator_id}")
        seen.add(device.elevator_id)

    rows: dict[int, StatusRow] = {}
    for row in statuses:
        if not isinstance(row, StatusRow):
            raise ValueError("상태 항목은 StatusRow여야 합니다.")
        if row.elevator_id in rows:
            raise ValueError(f"설비 {row.elevator_id}의 상태 행이 중복되었습니다.")
        rows[row.elevator_id] = row

    by_station: dict[int, list[ElevatorJudgment]] = {}
    for device in device_list:
        judged = _judge_elevator(device, rows.get(device.elevator_id), now_utc, ttl)
        by_station.setdefault(device.station_id, []).append(judged)

    return [
        _judge_station(station_id, sorted(items, key=lambda e: e.elevator_id))
        for station_id, items in sorted(by_station.items())
    ]


def judge_station_without_elevators(station_id: int) -> StationJudgment:
    """설비가 한 개도 없는 역의 판정. 설비 목록에 나타나지 않는 역에 쓴다(계약 6.2절 T = 0)."""
    return _judge_station(station_id, [])
