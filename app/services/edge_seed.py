"""검증된 역 간선(station_edges) 적재. 이슈 #30, #31.

원칙
- data/seed/station_edges_verified.csv만 읽는다. 검토 파일(station_edges_review.csv)은 읽지 않는다.
- 로더가 입력을 다시 검증한다. 생성기를 믿고 통과시키지 않는다.
  ADJACENT: 같은 노선, 공식 역번호가 1 차이, 정해진 source. TRANSFER: 노선이 다르고 확정된 환승 그룹 안의 쌍.
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
SOURCE_ADJACENT = "OA-12035+FE-MOCK"
SOURCE_TRANSFER = "OA-13290"
ALLOWED_SOURCE = {"ADJACENT": SOURCE_ADJACENT, "TRANSFER": SOURCE_TRANSFER}
VERIFIED_FILE = "station_edges_verified.csv"


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


def read_verified_edges(seed_dir: Path = DEFAULT_SEED_DIR) -> list[EdgeRow]:
    """검증된 간선 파일을 읽고 규칙을 다시 확인한다. 하나라도 어긋나면 SeedError다."""
    seed_rows = read_seed_rows(seed_dir / "stations_oa12035.csv")
    line_of = {r.code: r.line_id for r in seed_rows}
    review = read_review(seed_dir / "station_review.csv", seed_rows)
    group_pairs = {
        frozenset(pair)
        for group in review.confirmed_groups
        for pair in _pairs(group.codes)
    }

    path = seed_dir / VERIFIED_FILE
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
        if edge.edge_type not in ALLOWED_SOURCE:
            raise SeedError(f"{where}: 허용되지 않은 edge_type {edge.edge_type!r}")
        if edge.source != ALLOWED_SOURCE[edge.edge_type]:
            raise SeedError(f"{where}: source가 {ALLOWED_SOURCE[edge.edge_type]!r}이어야 합니다: {edge.source!r}")
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
        edges.append(edge)
    return edges


def _pairs(codes):
    ordered = sorted(codes)
    return [(a, b) for i, a in enumerate(ordered) for b in ordered[i + 1:]]


def load_edges(session: Session, edges: list[EdgeRow], *, dry_run: bool = False) -> EdgeReport:
    """간선을 적재한다. 같은 입력을 다시 넣어도 행이 늘지 않는다. 실패하면 롤백하고 예외를 던진다."""
    report = EdgeReport(dry_run=dry_run)
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
