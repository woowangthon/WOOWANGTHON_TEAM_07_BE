"""OA-15994 엘리베이터 Upsert 적재.

파서(app/clients/oa15994.py)가 만든 ElevatorRecord를 elevators, elevator_status에 저장한다.
이 모듈은 DB 쓰기만 한다. 외부 API 호출, 파일 읽기, 시계 읽기를 하지 않는다.

fetched_at (중요)
- 호출자가 주입하는 UTC 시각 문자열(예: 2026-11-14T03:00:00Z)이다. 이 모듈은 현재 시각을 읽지 않는다.
- "우리가 응답을 가져왔거나 적재한 시각"을 기록하는 필드다. 설비의 상태가 그 시각에 관측되었다는
  뜻이 아니다. OA-15994 응답에는 원본 관측 시각이 없으므로 observed_at은 항상 NULL로 둔다.
- 샘플 파일(docs/samples)의 날짜나 수정 시각을 수집 시각으로 쓰지 않는다. 샘플을 적재하는 테스트도
  임의로 정한 시각을 주입할 뿐이며, 그 값이 실제 최신 수집 시각이라는 뜻이 아니다.
- 실제 data/dev.db에 샘플을 적재하는 경로는 만들지 않는다.

규칙
- 설비 식별자: (source, external_station_code, elevator_name). source는 OA-15994다.
- 역 연결: station_external_ids(source=OA-15994, external_station_code) -> station_id.
  매핑이 없는 코드의 설비는 건너뛰고 리포트에 남긴다. 환승역의 여러 외부 코드는 서로 다른 식별자다.
- 기존 설비의 elevator_id를 보존한다. location_description, operation_section이 바뀌면 갱신한다.
  station_id가 바뀌어야 하면 매핑 충돌이므로 전체를 롤백한다.
- elevator_status는 설비당 최신 1행(PK=elevator_id)이다. 이력을 쌓지 않는다.
- 상태가 None(미확인)이면 설비 행만 저장한다. 상태 행을 만들거나 갱신하지 않고, 기존 상태 행이
  있으면 status, source_status_text, fetched_at을 모두 그대로 둔다. CHECK 제약을 피하려고 임의의
  가동 상태를 넣지 않는다.
- 입력에 없는 기존 설비는 삭제하지 않고 상태도 바꾸지 않는다.
- EV 입력이 0건이면 오류다. 아무것도 바꾸지 않는다.
- 전체를 한 트랜잭션으로 처리한다. 실패하면 롤백한다. dry_run이면 계산한 뒤 롤백한다.

세션과 트랜잭션 (호출자는 이 함수 전용의 독립된 세션을 써야 한다)
- ingest_elevators는 넘겨받은 세션의 트랜잭션 전체를 직접 commit/rollback한다. 설비 단위로
  나누거나 호출자의 다른 작업과 묶을 수 없다.
- 그래서 세션에 이 함수와 무관한 미커밋 변경(추가, 수정, 삭제)이 있으면 영향을 받는다.
    성공          : 호출자의 미커밋 변경도 함께 commit된다.
    dry_run       : 호출자의 미커밋 변경도 함께 rollback된다(변경이 사라진다).
    적재 중 오류  : 호출자의 미커밋 변경도 함께 rollback된다(변경이 사라진다).
                    (StationMappingConflictError, StaleFetchError 등 DB 접근 이후의 오류)
    검증 오류     : DB에 접근하기 전에 던진다. 호출자의 미커밋 변경은 commit도 rollback도 되지 않고
                    그대로 남는다. (EmptyInputError, InvalidFetchedAtError, 중복 식별자, 형식 오류)
- 따라서 이 함수를 부를 때는 다른 작업이 없는 새 세션을 만들어 넘기고, 호출자의 다른 변경은
  별도 세션에서 처리한다.
- SQLite는 쓰기 잠금이 하나뿐이다. 호출자의 다른 세션에 이미 flush한 미커밋 쓰기가 있으면 이 함수의
  쓰기가 "database is locked"로 막혀 실패한다(적재는 롤백된다). 독립된 세션을 쓰더라도 다른 세션의
  미커밋 쓰기는 먼저 commit하거나 rollback해 두어야 한다.

이 모듈에서 하지 않는 일 (Issue #25 CLI에서 구현한다)
- 샘플 입력을 실제 DB(data/dev.db)에 적재하지 못하게 막는 방어.
- 모든 외부 역 코드가 미매핑인 입력을 실패로 처리하는 것. 이 함수는 그 경우 아무것도 저장하지 않고
  리포트에 미매핑으로만 남긴다(오류가 아니다).
- elevators.station_line_id 추가. 현재 스키마(station_id까지만 연결)를 유지하며, #25 구현 전에
  기존 스키마와 매핑 근거를 검토하고 별도 승인을 받은 뒤에만 추가한다.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.oa15994 import ElevatorRecord
from app.models import Elevator, ElevatorStatus, StationExternalId
from app.timeutil import parse_utc_iso, to_utc_iso

SOURCE = "OA-15994"
ALLOWED_STATUSES = frozenset({"가동중", "가동 안됨"})


class ElevatorIngestError(Exception):
    """적재 규칙 위반. 메시지에는 키나 응답 본문을 넣지 않는다."""


class InvalidFetchedAtError(ElevatorIngestError, ValueError):
    """fetched_at이 UTC 저장 형식(YYYY-MM-DDTHH:MM:SSZ)이 아닐 때."""


class EmptyInputError(ElevatorIngestError):
    """EV 설비 입력이 0건일 때. 빈 응답으로 기존 데이터를 훼손하지 않기 위해 오류로 본다."""


class StationMappingConflictError(ElevatorIngestError):
    """이미 저장된 설비의 station_id와 현재 매핑이 달라질 때."""


class StaleFetchError(ElevatorIngestError):
    """주입한 fetched_at이 이미 저장된 상태의 fetched_at보다 과거일 때(오래된 실행이 최신 값을 덮는 것을 막는다)."""


@dataclass(frozen=True)
class UnmappedElevator:
    station_code: str
    station_name: str
    elevator_name: str


@dataclass
class IngestReport:
    dry_run: bool
    fetched_at: str
    input_count: int
    elevators_created: int = 0
    elevators_updated: int = 0
    elevators_unchanged: int = 0
    status_created: int = 0
    status_updated: int = 0
    status_unchanged: int = 0
    # 상태가 None인(미확인) 입력 중 역이 매핑된 건수. 상태 행은 만들거나 갱신하지 않는다.
    status_unknown: int = 0
    # 역 코드가 station_external_ids(source=OA-15994)에 없어 건너뛴 설비
    unmapped: list[UnmappedElevator] = field(default_factory=list)

    @property
    def unmapped_count(self) -> int:
        return len(self.unmapped)

    @property
    def unmapped_station_codes(self) -> list[str]:
        return sorted({u.station_code for u in self.unmapped})

    def as_counts(self) -> dict[str, int]:
        return {
            "input": self.input_count,
            "elevators_created": self.elevators_created,
            "elevators_updated": self.elevators_updated,
            "elevators_unchanged": self.elevators_unchanged,
            "status_created": self.status_created,
            "status_updated": self.status_updated,
            "status_unchanged": self.status_unchanged,
            "status_unknown": self.status_unknown,
            "unmapped": self.unmapped_count,
        }


def validate_fetched_at(value: object) -> str:
    """UTC 저장 형식(예: 2026-11-14T03:00:00Z)만 받는다. 다른 형식과 오프셋 표기는 거부한다."""
    if not isinstance(value, str):
        raise InvalidFetchedAtError("fetched_at은 문자열이어야 합니다.")
    try:
        parsed = parse_utc_iso(value)
    except ValueError:
        raise InvalidFetchedAtError("fetched_at이 UTC 형식(YYYY-MM-DDTHH:MM:SSZ)이 아닙니다.") from None
    if to_utc_iso(parsed) != value:
        raise InvalidFetchedAtError("fetched_at은 YYYY-MM-DDTHH:MM:SSZ 형식이어야 합니다.")
    return value


def _validate_records(records: list[ElevatorRecord]) -> None:
    if not records:
        raise EmptyInputError("EV 설비 입력이 0건입니다. 기존 데이터를 바꾸지 않습니다.")
    seen: set[tuple[str, str]] = set()
    for index, record in enumerate(records):
        if not isinstance(record, ElevatorRecord):
            raise ElevatorIngestError(f"records[{index}]가 ElevatorRecord가 아닙니다.")
        if record.status is not None and record.status not in ALLOWED_STATUSES:
            raise ElevatorIngestError(f"records[{index}]의 status가 허용 값이 아닙니다.")
        if record.key in seen:
            raise ElevatorIngestError(f"records[{index}]의 식별자(역 코드, 설비명)가 중복되었습니다.")
        seen.add(record.key)


def ingest_elevators(
    session: Session,
    records: Sequence[ElevatorRecord],
    *,
    fetched_at: str,
    dry_run: bool = False,
) -> IngestReport:
    """ElevatorRecord를 Upsert한다. 실패하면 롤백하고 예외를 올린다. dry_run이면 항상 롤백한다.

    주의: 세션의 트랜잭션 전체를 commit 또는 rollback한다. 이 함수 전용의 독립된 세션을 넘겨야 하며,
    세션에 다른 미커밋 변경이 있으면 성공 시 함께 commit되고, dry_run이나 적재 중 오류 시 함께
    rollback된다. 검증 오류(빈 입력, 잘못된 fetched_at, 중복 식별자)는 DB 접근 전에 발생하므로
    미커밋 변경을 건드리지 않는다. 자세한 내용은 모듈 docstring을 본다.
    """
    stamp = validate_fetched_at(fetched_at)
    batch = list(records)
    _validate_records(batch)  # 여기서 실패하면 DB를 건드리지 않는다

    report = IngestReport(dry_run=dry_run, fetched_at=stamp, input_count=len(batch))
    try:
        _apply(session, batch, stamp, report)
        if dry_run:
            session.rollback()
        else:
            session.commit()
    except Exception:
        session.rollback()
        raise
    return report


def _apply(session: Session, records: list[ElevatorRecord], stamp: str, report: IngestReport) -> None:
    codes = {r.station_code for r in records}
    mapping: dict[str, int] = dict(
        session.execute(
            select(StationExternalId.external_station_code, StationExternalId.station_id).where(
                StationExternalId.source == SOURCE,
                StationExternalId.external_station_code.in_(codes),
            )
        ).all()
    )

    mapped: list[ElevatorRecord] = []
    for record in records:
        if record.station_code in mapping:
            mapped.append(record)
        else:
            report.unmapped.append(
                UnmappedElevator(record.station_code, record.station_name, record.elevator_name)
            )

    existing: dict[tuple[str, str], Elevator] = {}
    if mapped:
        existing = {
            (e.external_station_code, e.elevator_name): e
            for e in session.scalars(
                select(Elevator).where(
                    Elevator.source == SOURCE,
                    Elevator.external_station_code.in_({r.station_code for r in mapped}),
                )
            )
        }

    # 1) 설비: 없으면 만들고, 있으면 위치·구간만 갱신한다. station_id가 달라지면 충돌이다.
    elevator_by_key: dict[tuple[str, str], Elevator] = {}
    for record in mapped:
        station_id = mapping[record.station_code]
        elevator = existing.get(record.key)
        if elevator is None:
            elevator = Elevator(
                station_id=station_id,
                source=SOURCE,
                external_station_code=record.station_code,
                elevator_name=record.elevator_name,
                location_description=record.location_description,
                operation_section=record.operation_section,
            )
            session.add(elevator)
            report.elevators_created += 1
        else:
            if elevator.station_id != station_id:
                raise StationMappingConflictError(
                    f"역 코드 {record.station_code}의 설비가 이미 다른 역(station_id={elevator.station_id})에 "
                    f"저장되어 있어 현재 매핑(station_id={station_id})과 충돌합니다. 전체를 롤백합니다."
                )
            changed = False
            if elevator.location_description != record.location_description:
                elevator.location_description = record.location_description
                changed = True
            if elevator.operation_section != record.operation_section:
                elevator.operation_section = record.operation_section
                changed = True
            if changed:
                report.elevators_updated += 1
            else:
                report.elevators_unchanged += 1
        elevator_by_key[record.key] = elevator
    session.flush()  # 새 설비의 elevator_id를 확보한다

    # 2) 상태: 설비당 최신 1행. 미확인(None)은 만들지도 갱신하지도 않는다.
    ids = [e.elevator_id for e in elevator_by_key.values()]
    statuses: dict[int, ElevatorStatus] = {}
    if ids:
        statuses = {
            s.elevator_id: s
            for s in session.scalars(select(ElevatorStatus).where(ElevatorStatus.elevator_id.in_(ids)))
        }
    incoming = parse_utc_iso(stamp)
    for record in mapped:
        if record.status is None:
            report.status_unknown += 1
            continue
        elevator = elevator_by_key[record.key]
        current = statuses.get(elevator.elevator_id)
        if current is None:
            session.add(
                ElevatorStatus(
                    elevator_id=elevator.elevator_id,
                    status=record.status,
                    source_status_text=record.source_status_text,
                    observed_at=None,  # 원본에 관측 시각이 없다
                    fetched_at=stamp,
                    source=SOURCE,
                )
            )
            report.status_created += 1
            continue
        try:
            stored = parse_utc_iso(current.fetched_at)
        except ValueError:
            raise ElevatorIngestError(
                f"기존 상태의 fetched_at 형식이 올바르지 않습니다(elevator_id={elevator.elevator_id})."
            ) from None
        if incoming < stored:
            raise StaleFetchError(
                f"주입한 fetched_at({stamp})이 저장된 값({current.fetched_at})보다 과거입니다. 전체를 롤백합니다."
            )
        if (
            current.status == record.status
            and current.source_status_text == record.source_status_text
            and current.fetched_at == stamp
            and current.source == SOURCE
        ):
            report.status_unchanged += 1
        else:
            current.status = record.status
            current.source_status_text = record.source_status_text
            current.fetched_at = stamp
            current.source = SOURCE
            report.status_updated += 1
    session.flush()
