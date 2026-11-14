"""역 간선(station_edges) 적재. 이슈 #30, #31.

검증 등급
- VERIFIED: 공식 출처가 관계를 직접 증명한 간선. 현재는 OA-13290 환승 기록의 TRANSFER뿐이다.
- PROVISIONAL: 프론트 순서와 공식 역번호 연속성이 일치하는 ADJACENT. 공식 인접 증명이 아니다.
  기본 읽기·적재 대상이 아니다. 명시적으로 allow_provisional=True를 줄 때만 적재할 수 있고,
  CLI에는 이 옵션이 없다(별도 결정 전까지 일반 --apply로는 적재할 수 없다).

원칙
- 기본 입력은 data/seed/station_edges_verified.csv뿐이다. 검토 파일(station_edges_review.csv)은 읽지 않는다.
- 로더가 입력을 다시 검증한다. 생성기를 믿고 통과시키지 않는다.
  TRANSFER: 노선이 다르고, 확정된 환승 그룹 안의 쌍이며, OA-13290 공식 기록이 양방향으로 있다.
  확정 그룹의 모든 쌍이 빠짐없이 있어야 한다.
  ADJACENT(PROVISIONAL): 같은 노선, 공식 역번호가 1 차이, 정해진 source. 공식 근거로 취급하지 않는다.
- 역 시드(station_seed)가 먼저 적재되어 있어야 한다. 역·노선·역 병합을 만들거나 바꾸지 않는다.
- 한 트랜잭션에서 처리한다. 매핑 실패나 충돌이 있으면 전체를 롤백한다. 기존 행은 삭제·덮어쓰지 않는다.
- 방향 정책: 한 쌍은 한 행이다(from은 역번호가 작은 쪽). 반대 방향 행이 이미 있으면 같은 간선으로 보고
  새로 만들지 않는다. 소비하는 쪽은 무방향으로 취급한다.
- 외부 API를 호출하지 않는다.
"""

import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import StationEdge, StationExternalId, StationLine
from app.services.station_seed import (
    DEFAULT_SEED_DIR,
    SOURCE_OA12035,
    ConflictError,
    SeedError,
    read_review,
    read_seed_rows,
)

EDGE_HEADER = ["edge_type", "from_code", "from_line", "to_code", "to_line", "source", "evidence"]
SOURCE_ADJACENT_PROVISIONAL = "OA-12035+FE-MOCK"  # 공식 인접 증명이 아니다
SOURCE_TRANSFER = "OA-13290"
OFFICIAL_SOURCE = {"TRANSFER": SOURCE_TRANSFER}  # 공식 인접역 자료가 없어 ADJACENT는 비어 있다
PROVISIONAL_SOURCE = {"ADJACENT": SOURCE_ADJACENT_PROVISIONAL}
VERIFIED_FILE = "station_edges_verified.csv"
PROVISIONAL_FILE = "station_edges_provisional.csv"
EVIDENCE_FILE = "transfer_evidence_oa13290.csv"


@dataclass(frozen=True)
class EdgeRow:
    edge_type: str
    from_code: str
    from_line: str
    to_code: str
    to_line: str
    source: str


@dataclass
class EdgeReport:
    dry_run: bool = False
    counts: Counter = field(default_factory=Counter)


def _official(edge: "EdgeRow") -> bool:
    return OFFICIAL_SOURCE.get(edge.edge_type) == edge.source


def read_verified_edges(seed_dir: Path = DEFAULT_SEED_DIR) -> list[EdgeRow]:
    """VERIFIED 파일(TRANSFER만)을 읽고 규칙을 다시 확인한다. 하나라도 어긋나면 SeedError다."""
    return _read_edges(seed_dir, VERIFIED_FILE, OFFICIAL_SOURCE, provisional=False)


def read_provisional_edges(seed_dir: Path = DEFAULT_SEED_DIR) -> list[EdgeRow]:
    """PROVISIONAL 파일(ADJACENT)을 읽는다. 공식 근거가 아니므로 기본 로더는 이 함수를 쓰지 않는다."""
    return _read_edges(seed_dir, PROVISIONAL_FILE, PROVISIONAL_SOURCE, provisional=True)


def _read_edges(seed_dir: Path, filename: str, allowed_source: dict[str, str], *, provisional: bool) -> list[EdgeRow]:
    seed_rows = read_seed_rows(seed_dir / "stations_oa12035.csv")
    line_of = {r.code: r.line_id for r in seed_rows}
    review = read_review(seed_dir / "station_review.csv", seed_rows)
    group_pairs = {
        frozenset(pair)
        for group in review.confirmed_groups
        for pair in _pairs(group.codes)
    }
    official_pairs = _official_transfer_records(seed_dir / EVIDENCE_FILE)

    path = seed_dir / filename
    try:
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != EDGE_HEADER:
                raise SeedError(f"{path.name}: 헤더가 다릅니다. 기대={EDGE_HEADER} 실제={reader.fieldnames}")
            raw = list(reader)
    except FileNotFoundError as exc:
        raise SeedError(f"파일이 없습니다: {path}") from exc

    edges: list[EdgeRow] = []
    seen: set[frozenset[str]] = set()
    for n, r in enumerate(raw, start=2):
        where = f"{path.name} {n}행"
        edge = EdgeRow(r["edge_type"], r["from_code"], r["from_line"], r["to_code"], r["to_line"], r["source"])
        if edge.edge_type not in allowed_source:
            if edge.edge_type == "ADJACENT":
                raise SeedError(f"{where}: 공식 근거가 없는 ADJACENT는 이 파일에 둘 수 없습니다(PROVISIONAL 파일 전용).")
            raise SeedError(f"{where}: 허용되지 않은 edge_type {edge.edge_type!r}")
        if edge.source != allowed_source[edge.edge_type]:
            raise SeedError(f"{where}: source가 {allowed_source[edge.edge_type]!r}이어야 합니다: {edge.source!r}")
        for code, line in ((edge.from_code, edge.from_line), (edge.to_code, edge.to_line)):
            if line_of.get(code) != line:
                raise SeedError(f"{where}: 시드에 없는 코드이거나 노선이 다릅니다: {code}({line})")
        if edge.from_code >= edge.to_code:
            raise SeedError(f"{where}: from_code는 to_code보다 작아야 합니다(한 쌍은 한 행): {edge.from_code}, {edge.to_code}")
        pair = frozenset({edge.from_code, edge.to_code})
        if pair in seen:
            raise SeedError(f"{where}: 같은 쌍이 중복되었습니다: {edge.from_code}, {edge.to_code}")
        seen.add(pair)
        if edge.edge_type == "ADJACENT":
            if edge.from_line != edge.to_line:
                raise SeedError(f"{where}: ADJACENT는 같은 노선이어야 합니다.")
            if int(edge.to_code) - int(edge.from_code) != 1:
                raise SeedError(f"{where}: ADJACENT는 공식 역번호가 연속해야 합니다: {edge.from_code}, {edge.to_code}")
        else:
            if edge.from_line == edge.to_line:
                raise SeedError(f"{where}: TRANSFER는 서로 다른 노선이어야 합니다.")
            if pair not in group_pairs:
                raise SeedError(f"{where}: 확정된 환승 그룹의 쌍이 아닙니다: {edge.from_code}, {edge.to_code}")
            if (edge.from_code, edge.to_code) not in official_pairs or (edge.to_code, edge.from_code) not in official_pairs:
                raise SeedError(f"{where}: OA-13290 공식 기록이 양방향으로 없습니다: {edge.from_code}, {edge.to_code}")
        edges.append(edge)

    if not provisional:
        missing = group_pairs - seen
        if missing:
            raise SeedError(f"{path.name}: 확정 그룹의 환승 쌍이 빠졌습니다: {sorted(tuple(sorted(m)) for m in missing)[:3]}")
    return edges


def _official_transfer_records(path: Path) -> set[tuple[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as f:
            return {(r["from_code"], r["to_code"]) for r in csv.DictReader(f)}
    except FileNotFoundError as exc:
        raise SeedError(f"파일이 없습니다: {path}") from exc


def _pairs(codes):
    ordered = sorted(codes)
    return [(a, b) for i, a in enumerate(ordered) for b in ordered[i + 1:]]


def load_edges(
    session: Session, edges: list[EdgeRow], *, dry_run: bool = False, allow_provisional: bool = False
) -> EdgeReport:
    """간선을 적재한다. 같은 입력을 다시 넣어도 행이 늘지 않는다. 실패하면 롤백하고 예외를 던진다.

    공식 근거가 없는 간선(PROVISIONAL ADJACENT)은 allow_provisional=True가 아니면 DB에 손대기 전에 거부한다.
    """
    report = EdgeReport(dry_run=dry_run)
    if not allow_provisional:
        bad = [e for e in edges if not _official(e)]
        if bad:
            raise SeedError(
                f"공식 근거가 없는 간선 {len(bad)}건이 입력에 있어 적재하지 않습니다"
                f"(예: {bad[0].edge_type} {bad[0].from_code}-{bad[0].to_code}, source={bad[0].source})."
            )
    try:
        resolved = {
            code: (line_id, station_id)
            for code, line_id, station_id in session.execute(
                select(StationExternalId.external_station_code, StationExternalId.station_line_id, StationExternalId.station_id)
                .where(StationExternalId.source == SOURCE_OA12035)
            )
        }
        line_rows = {
            sl_id: (station_id, line_id)
            for sl_id, station_id, line_id in session.execute(
                select(StationLine.station_line_id, StationLine.station_id, StationLine.line_id)
            )
        }
        existing: dict[tuple[int, int], tuple[str, str | None]] = {
            (a, b): (t, s)
            for a, b, t, s in session.execute(
                select(StationEdge.from_station_line_id, StationEdge.to_station_line_id, StationEdge.edge_type, StationEdge.source)
            )
        }

        for edge in edges:
            ids = []
            for code, line in ((edge.from_code, edge.from_line), (edge.to_code, edge.to_line)):
                found = resolved.get(code)
                if found is None or found[0] is None:
                    raise SeedError(f"역 시드가 적재되지 않았거나 노선 연결이 없습니다: 코드 {code}")
                sl_id = found[0]
                if line_rows[sl_id][1] != line:
                    raise SeedError(f"코드 {code}의 노선이 DB와 다릅니다.")
                ids.append(sl_id)
            from_id, to_id = ids
            from_station, to_station = line_rows[from_id][0], line_rows[to_id][0]
            if edge.edge_type == "ADJACENT" and from_station == to_station:
                raise SeedError(f"ADJACENT가 같은 역 안의 연결입니다: {edge.from_code}, {edge.to_code}")
            if edge.edge_type == "TRANSFER" and from_station != to_station:
                raise SeedError(f"TRANSFER가 서로 다른 역을 잇습니다(역 병합이 적용되지 않음): {edge.from_code}, {edge.to_code}")

            key = "adjacent" if edge.edge_type == "ADJACENT" else "transfer"
            found_edge = existing.get((from_id, to_id)) or existing.get((to_id, from_id))
            if found_edge is not None:
                if found_edge != (edge.edge_type, edge.source):
                    raise ConflictError(
                        f"기존 간선과 충돌합니다(덮어쓰지 않음): {edge.from_code}, {edge.to_code} "
                        f"기존={found_edge} 입력={(edge.edge_type, edge.source)}"
                    )
                report.counts[f"{key}_unchanged"] += 1
                continue
            session.add(StationEdge(
                from_station_line_id=from_id, to_station_line_id=to_id,
                edge_type=edge.edge_type, source=edge.source,
            ))
            existing[(from_id, to_id)] = (edge.edge_type, edge.source)
            report.counts[f"{key}_created"] += 1

        session.flush()
        if dry_run:
            session.rollback()
        else:
            session.commit()
    except Exception:
        session.rollback()
        raise
    return report
