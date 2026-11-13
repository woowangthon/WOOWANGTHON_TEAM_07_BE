"""OA-12035 기반 1~8호선 역·노선 시드 적재.

원칙
- 로컬 시드 파일만 읽는다. 외부 API를 호출하지 않는다.
- 한 트랜잭션에서 처리한다. 충돌이 있으면 전체를 롤백하고 아무것도 덮어쓰지 않는다.
- 역명이나 주소가 같다는 이유로 역을 병합하지 않는다. 병합은 review 파일에서
  사람이 CONFIRMED로 기록한 그룹만 한다.
- 시드에 없는 기존 행은 삭제하지 않는다.
- OA-15994 코드는 external_code_links.csv에 기록된(대조로 확인된) 코드만 연결한다.
- 이 모듈은 1~8호선 전체를 확보했다고 가정하지 않는다. 1호선은 일부 구간만 있다(manifest 참고).
"""

import csv
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Line, Station, StationExternalId, StationLine

OPERATOR = "서울교통공사"  # OA-12035 출처 범위에 한해 설정한다.
SOURCE_OA12035 = "OA-12035"
VALID_LINES = frozenset("12345678")
DEFAULT_SEED_DIR = Path(__file__).resolve().parents[2] / "data" / "seed"

_CODE_PATTERN = re.compile(r"\d{4}")
_TRANSFER_STATUSES = {"PENDING", "CONFIRMED", "REJECTED"}
_NAME_VARIANT_STATUSES = {"PENDING", "ACCEPTED_BY_CODE", "CONFIRMED", "REJECTED"}


class SeedError(Exception):
    """시드 파일이나 review 파일이 규칙에 맞지 않을 때."""


class ConflictError(SeedError):
    """기존 DB 내용과 충돌해서 적재를 중단해야 할 때. 호출한 쪽에서 롤백한다."""


@dataclass(frozen=True)
class SeedRow:
    code: str
    line_id: str
    station_name: str


@dataclass(frozen=True)
class CodeLink:
    source: str
    code: str


@dataclass(frozen=True)
class ConfirmedGroup:
    codes: tuple[str, ...]
    station_name: str


@dataclass
class ReviewSummary:
    confirmed_groups: list[ConfirmedGroup] = field(default_factory=list)
    transfer_pending: int = 0
    name_variant_pending: int = 0


@dataclass
class LoadReport:
    dry_run: bool = False
    counts: Counter = field(default_factory=Counter)
    review: ReviewSummary = field(default_factory=ReviewSummary)

    def created(self, key: str) -> None:
        self.counts[f"{key}_created"] += 1

    def unchanged(self, key: str) -> None:
        self.counts[f"{key}_unchanged"] += 1

    def backfilled(self, key: str) -> None:
        self.counts[f"{key}_backfilled"] += 1


# ---------- 파일 읽기 ----------


def _read_csv(path: Path, expected_header: list[str]) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != expected_header:
                raise SeedError(f"{path.name}: 헤더가 다릅니다. 기대={expected_header} 실제={reader.fieldnames}")
            return list(reader)
    except FileNotFoundError as exc:
        raise SeedError(f"파일이 없습니다: {path}") from exc


def read_seed_rows(path: Path) -> list[SeedRow]:
    raw = _read_csv(path, ["external_station_code", "line_id", "station_name", "source_serial"])
    rows: list[SeedRow] = []
    seen: set[str] = set()
    for r in raw:
        code, line_id, name = r["external_station_code"], r["line_id"], r["station_name"].strip()
        if not _CODE_PATTERN.fullmatch(code):
            raise SeedError(f"역 코드 형식 오류(4자리 숫자여야 함): {code!r}")
        if line_id not in VALID_LINES:
            raise SeedError(f"1~8호선이 아닌 노선: {line_id!r} (코드 {code})")
        if not name:
            raise SeedError(f"역명이 비어 있습니다: 코드 {code}")
        if code in seen:
            raise SeedError(f"역 코드가 중복되었습니다: {code}")
        seen.add(code)
        rows.append(SeedRow(code=code, line_id=line_id, station_name=name))
    return rows


def read_code_links(path: Path, seed_codes: set[str]) -> list[CodeLink]:
    raw = _read_csv(path, ["source", "external_station_code", "basis"])
    links: list[CodeLink] = []
    seen: set[tuple[str, str]] = set()
    for r in raw:
        source, code = r["source"].strip(), r["external_station_code"]
        if not source:
            raise SeedError(f"source가 비어 있습니다: 코드 {code}")
        if source == SOURCE_OA12035:
            raise SeedError("OA-12035 코드는 시드 파일로 적재하므로 연결 목록에 둘 수 없습니다.")
        if code not in seed_codes:
            raise SeedError(f"연결 목록의 코드가 시드에 없습니다: {source} {code}")
        if not r["basis"].strip():
            raise SeedError(f"연결 근거가 비어 있습니다: {source} {code}")
        if (source, code) in seen:
            raise SeedError(f"연결 목록이 중복되었습니다: {source} {code}")
        seen.add((source, code))
        links.append(CodeLink(source=source, code=code))
    return links


def read_review(path: Path, rows: list[SeedRow]) -> ReviewSummary:
    """review 파일을 검증하고 CONFIRMED 환승 그룹만 꺼낸다. PENDING은 적재에 영향을 주지 않는다."""
    raw = _read_csv(path, ["review_type", "codes", "proposed_station_name", "status", "note"])
    line_by_code = {r.code: r.line_id for r in rows}
    summary = ReviewSummary()
    grouped: set[str] = set()
    for r in raw:
        rtype, status = r["review_type"], r["status"]
        codes = tuple(c for c in r["codes"].split("|") if c)
        unknown = [c for c in codes if c not in line_by_code]
        if unknown:
            raise SeedError(f"review 파일의 코드가 시드에 없습니다: {unknown}")
        if rtype == "TRANSFER_CANDIDATE":
            if status not in _TRANSFER_STATUSES:
                raise SeedError(f"환승 후보의 status가 올바르지 않습니다: {status!r}")
            if len(codes) < 2:
                raise SeedError(f"환승 후보는 코드가 2개 이상이어야 합니다: {r['codes']!r}")
            if status == "PENDING":
                summary.transfer_pending += 1
            elif status == "CONFIRMED":
                name = r["proposed_station_name"].strip()
                if not name:
                    raise SeedError(f"CONFIRMED 그룹에는 proposed_station_name이 필요합니다: {r['codes']!r}")
                lines = [line_by_code[c] for c in codes]
                if len(set(lines)) != len(lines):
                    raise SeedError(f"CONFIRMED 그룹에 같은 노선의 코드가 둘 이상 있습니다: {r['codes']!r}")
                overlap = grouped & set(codes)
                if overlap:
                    raise SeedError(f"코드가 둘 이상의 CONFIRMED 그룹에 속합니다: {sorted(overlap)}")
                grouped |= set(codes)
                summary.confirmed_groups.append(ConfirmedGroup(codes=codes, station_name=name))
        elif rtype == "NAME_VARIANT":
            if status not in _NAME_VARIANT_STATUSES:
                raise SeedError(f"역명 차이의 status가 올바르지 않습니다: {status!r}")
            if len(codes) != 1:
                raise SeedError(f"역명 차이는 코드가 정확히 1개여야 합니다: {r['codes']!r}")
            if status == "PENDING":
                summary.name_variant_pending += 1
        else:
            raise SeedError(f"알 수 없는 review_type: {rtype!r}")
    return summary


def read_seed_dir(seed_dir: Path) -> tuple[list[SeedRow], list[CodeLink], ReviewSummary]:
    rows = read_seed_rows(seed_dir / "stations_oa12035.csv")
    links = read_code_links(seed_dir / "external_code_links.csv", {r.code for r in rows})
    review = read_review(seed_dir / "station_review.csv", rows)
    return rows, links, review


# ---------- 적재 ----------


def _build_groups(
    rows: list[SeedRow], confirmed: list[ConfirmedGroup]
) -> list[tuple[str, list[SeedRow]]]:
    """시드 순서를 따라 그룹을 만든다. CONFIRMED가 아닌 코드는 모두 단독 그룹이다."""
    by_code = {r.code: r for r in rows}
    group_of: dict[str, ConfirmedGroup] = {c: g for g in confirmed for c in g.codes}
    emitted: set[ConfirmedGroup] = set()
    groups: list[tuple[str, list[SeedRow]]] = []
    for r in rows:
        g = group_of.get(r.code)
        if g is None:
            groups.append((r.station_name, [r]))
        elif g not in emitted:
            emitted.add(g)
            groups.append((g.station_name, [by_code[c] for c in g.codes]))
    return groups


def _ensure_lines(session: Session, rows: list[SeedRow], report: LoadReport) -> None:
    for line_id in sorted({r.line_id for r in rows}):
        expected_name = f"{line_id}호선"
        line = session.get(Line, line_id)
        if line is None:
            session.add(Line(line_id=line_id, line_name=expected_name, operator=None))
            report.created("lines")
        elif line.line_name != expected_name:
            raise ConflictError(
                f"노선 {line_id}의 이름이 기존 값과 다릅니다: 기존={line.line_name!r} 시드={expected_name!r}"
            )
        else:
            report.unchanged("lines")
    session.flush()


def _load_group(
    session: Session, name: str, members: list[SeedRow], report: LoadReport
) -> None:
    codes = [m.code for m in members]
    existing = {
        e.external_station_code: e
        for e in session.scalars(
            select(StationExternalId).where(
                StationExternalId.source == SOURCE_OA12035,
                StationExternalId.external_station_code.in_(codes),
            )
        )
    }
    station_ids = {e.station_id for e in existing.values()}
    if len(station_ids) > 1:
        raise ConflictError(
            f"이미 서로 다른 역으로 적재된 코드를 하나의 그룹으로 합칠 수 없습니다: {codes}. "
            "기존 역을 합치는 병합 절차는 구현되어 있지 않습니다."
        )

    if station_ids:
        station = session.get(Station, next(iter(station_ids)))
        if station.station_name != name:
            raise ConflictError(
                f"역 {station.station_id}의 이름이 기존 값과 다릅니다: 기존={station.station_name!r} 시드={name!r}"
            )
        expected_lines = {m.line_id for m in members}
        extra = {
            sl.line_id
            for sl in session.scalars(
                select(StationLine).where(StationLine.station_id == station.station_id)
            )
        } - expected_lines
        if extra:
            raise ConflictError(
                f"역 {station.station_id}에 이 그룹에 없는 노선이 이미 연결되어 있습니다: {sorted(extra)}. "
                "확정 그룹이 바뀐 것으로 보이며 자동으로 분리하지 않습니다."
            )
        report.unchanged("stations")
    else:
        station = Station(station_name=name)
        session.add(station)
        session.flush()
        report.created("stations")

    for m in members:
        sl = session.scalar(
            select(StationLine).where(
                StationLine.station_id == station.station_id, StationLine.line_id == m.line_id
            )
        )
        if sl is None:
            sl = StationLine(station_id=station.station_id, line_id=m.line_id, operator=OPERATOR)
            session.add(sl)
            session.flush()
            report.created("station_lines")
        elif sl.operator is None:
            sl.operator = OPERATOR
            report.backfilled("station_lines")
        elif sl.operator != OPERATOR:
            raise ConflictError(
                f"station_line {sl.station_line_id}의 operator가 기존 값과 다릅니다: {sl.operator!r}"
            )
        else:
            report.unchanged("station_lines")
        _upsert_external_id(session, SOURCE_OA12035, m.code, sl, report)


def _upsert_external_id(
    session: Session, source: str, code: str, sl: StationLine, report: LoadReport
) -> None:
    key = f"external_ids_{source}"
    ext = session.scalar(
        select(StationExternalId).where(
            StationExternalId.source == source,
            StationExternalId.external_station_code == code,
        )
    )
    if ext is None:
        session.add(
            StationExternalId(
                source=source,
                external_station_code=code,
                station_id=sl.station_id,
                station_line_id=sl.station_line_id,
            )
        )
        session.flush()
        report.created(key)
        return
    if ext.station_id != sl.station_id:
        raise ConflictError(
            f"외부 코드 {source} {code}가 이미 다른 역(station_id={ext.station_id})에 연결되어 있습니다."
        )
    if ext.station_line_id is None:
        # 마이그레이션 전에 만들어진 행: NULL을 채울 뿐 기존 값을 바꾸지 않는다.
        ext.station_line_id = sl.station_line_id
        session.flush()
        report.backfilled(key)
    elif ext.station_line_id != sl.station_line_id:
        raise ConflictError(
            f"외부 코드 {source} {code}가 이미 다른 노선 행(station_line_id={ext.station_line_id})에 연결되어 있습니다."
        )
    else:
        report.unchanged(key)


def _link_codes(session: Session, links: list[CodeLink], report: LoadReport) -> None:
    for link in links:
        base = session.scalar(
            select(StationExternalId).where(
                StationExternalId.source == SOURCE_OA12035,
                StationExternalId.external_station_code == link.code,
            )
        )
        if base is None or base.station_line_id is None:
            raise ConflictError(f"연결할 OA-12035 코드 행이 없습니다: {link.code}")
        sl = session.get(StationLine, base.station_line_id)
        _upsert_external_id(session, link.source, link.code, sl, report)


def load_seed(
    session: Session,
    rows: list[SeedRow],
    links: list[CodeLink],
    review: ReviewSummary,
    *,
    dry_run: bool = False,
) -> LoadReport:
    """시드를 적재한다. 오류가 나면 롤백하고 예외를 그대로 올린다. dry_run이면 항상 롤백한다."""
    report = LoadReport(dry_run=dry_run, review=review)
    try:
        _ensure_lines(session, rows, report)
        for name, members in _build_groups(rows, review.confirmed_groups):
            _load_group(session, name, members, report)
        _link_codes(session, links, report)
        if dry_run:
            session.rollback()
        else:
            session.commit()
    except Exception:
        session.rollback()
        raise
    return report
