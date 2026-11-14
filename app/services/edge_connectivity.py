"""노선별 연결성 점검(#31). 검증된 ADJACENT 간선만으로 각 노선이 이어져 있는지 본다.

PENDING 간선은 DB에 없으므로 끊긴 구간으로 나타난다. 이는 오류가 아니라 아직 검증되지 않은 구간이다.
같은 노선 안의 ADJACENT 간선만 센다(환승 간선은 노선 사이 연결이라 노선별 연결성에 쓰지 않는다).
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import StationEdge, StationLine


@dataclass(frozen=True)
class LineConnectivity:
    line_id: str
    station_count: int
    component_sizes: tuple[int, ...]  # 큰 순서. 2개 이상이면 끊긴 구간이 있다
    isolated_station_line_ids: tuple[int, ...]  # 간선이 하나도 없는 역(노선 행)

    @property
    def connected(self) -> bool:
        return len(self.component_sizes) <= 1


def connectivity_report(session: Session) -> dict[str, LineConnectivity]:
    members: dict[str, list[int]] = {}
    for sl_id, line_id in session.execute(select(StationLine.station_line_id, StationLine.line_id)):
        members.setdefault(line_id, []).append(sl_id)
    line_of = {sl: ln for ln, ids in members.items() for sl in ids}

    parent = {sl: sl for sl in line_of}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    touched: set[int] = set()
    for a, b in session.execute(
        select(StationEdge.from_station_line_id, StationEdge.to_station_line_id)
        .where(StationEdge.edge_type == "ADJACENT")
    ):
        if line_of.get(a) is None or line_of.get(a) != line_of.get(b):
            continue
        touched.update((a, b))
        parent[find(a)] = find(b)

    report: dict[str, LineConnectivity] = {}
    for line_id, ids in sorted(members.items()):
        sizes: dict[int, int] = {}
        for sl in ids:
            root = find(sl)
            sizes[root] = sizes.get(root, 0) + 1
        report[line_id] = LineConnectivity(
            line_id=line_id,
            station_count=len(ids),
            component_sizes=tuple(sorted(sizes.values(), reverse=True)),
            isolated_station_line_ids=tuple(sorted(sl for sl in ids if sl not in touched)),
        )
    return report
