"""해커톤 시연용 데모 DB 생성. 실제 data/dev.db는 쓰지 않는다(읽기 전용 점검만 한다).

구성
1. 임시 디렉터리에 새 SQLite 파일을 만들고 Alembic head까지 마이그레이션한다.
2. 역·노선 시드를 적재한다(역 240, station_lines 276). 원본 DB와 같은 매핑인지 비교해 기록한다.
3. 공식 검증된 TRANSFER(OA-13290)를 적재한다.
4. 명시적으로 요청했을 때만 PROVISIONAL ADJACENT를 적재한다(데모 DB 전용 `allow_provisional`).
   운영용 로더(app.cli.load_edges)의 안전 제한은 바꾸지 않는다.
5. OA-15994 샘플 EV를 기존 수집 CLI의 파일 모드로 적재한다. 파일에는 수집·관측 시각이 없다.
   - observed_at은 만들지 않는다(항상 비어 있음).
   - fetched_at은 샘플 파일명의 날짜 00:00:00Z다. 실제 수집 시각이 아니라 날짜만 아는 값의 하한이고,
     현재 시각을 쓰지 않으므로 TTL(기본 120분)을 넘겨 STALE로 나온다. TTL을 우회하지 않는다.
6. 선택: 합성 시연 상태(DEMO/SYNTHETIC)를 실제 샘플과 별도 설비·상태 행으로 추가한다.
   source는 DEMO-SYNTHETIC, 설비명은 [DEMO/SYNTHETIC]로 시작한다. 실제 샘플 행은 건드리지 않는다.
   합성 상태의 fetched_at은 생성 시각이라 TTL 이후에는 자연히 STALE이 된다.
"""

import contextlib
import hashlib
import io
import json
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli import collect_elevators
from app.database import make_engine
from app.models import Elevator, ElevatorStatus, Station
from app.services.edge_seed import load_edges, read_provisional_edges, read_verified_edges
from app.services.station_seed import DEFAULT_SEED_DIR, load_seed, read_seed_dir
from app.timeutil import to_utc_iso, utc_now

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SAMPLE = ROOT / "docs" / "samples" / "OA-15994_SeoulMetroFaciInfo_2026-11-13.json"
SYNTHETIC_SOURCE = "DEMO-SYNTHETIC"
SYNTHETIC_PREFIX = "[DEMO/SYNTHETIC]"
SYNTHETIC_NOTE = "DEMO/SYNTHETIC 시연용 합성 데이터. 실제 상태가 아님"
DEMO_DB_NAME = "demo.db"
SAMPLE_DISCLAIMER = (
    "OA-15994 샘플은 2026-11-13에 저장한 과거 응답이며 현재 상태를 보장하지 않는다. "
    "원본에 관측·수집 시각이 없어 observed_at은 비어 있고, fetched_at은 파일명 날짜의 00:00:00Z(하한값)다. "
    "TTL 정책을 우회하지 않으므로 실제 시각 기준으로는 STALE로 보인다."
)

# 시연 시나리오: (역명, 합성 상태). 실제 시드의 역명과 정확히 일치해야 하며 하나의 역으로 해석되어야 한다.
SYNTHETIC_SCENARIO: tuple[tuple[str, str], ...] = (
    ("강남", "가동 안됨"),
    ("역삼", "가동중"),
    ("선릉", "가동 안됨"),
    ("교대(법원, 검찰청)", "가동중"),
    ("남부터미널(예술의전당)", "가동중"),
)


class DemoBuildError(Exception):
    pass


@dataclass
class DemoResult:
    out_dir: Path
    db_path: Path
    manifest: dict


def _temp_roots() -> list[Path]:
    roots = []
    for candidate in (tempfile.gettempdir(), "/tmp", "/var/tmp"):
        try:
            roots.append(Path(candidate).resolve())
        except OSError:
            continue
    return roots


def ensure_safe_output_dir(out_dir: Path) -> Path:
    """데모 DB는 임시 디렉터리 안의 새 위치에만 만든다. 저장소(특히 data/)와 기존 파일은 거부한다."""
    resolved = out_dir.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise DemoBuildError("저장소 안에는 데모 DB를 만들 수 없습니다(data/dev.db 보호).")
    if not any(root == resolved or root in resolved.parents for root in _temp_roots()):
        raise DemoBuildError("데모 DB는 임시 디렉터리 안에만 만들 수 있습니다.")
    if resolved.exists() and (not resolved.is_dir() or any(resolved.iterdir())):
        raise DemoBuildError("출력 디렉터리가 비어 있지 않습니다(덮어쓰지 않음).")
    return resolved


def default_fetched_at(sample: Path) -> str:
    """샘플 파일명의 날짜(YYYY-MM-DD)로 하한값을 만든다. 날짜가 없으면 명시값이 필요하다."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", sample.name)
    if not m:
        raise DemoBuildError("샘플 파일명에 날짜가 없어 --sample-fetched-at을 명시해야 합니다.")
    return f"{m.group(1)}T00:00:00Z"


def _ro_connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)


_COUNT_QUERIES = {
    "stations": "select count(*) from stations",
    "station_lines": "select count(*) from station_lines",
    "edges_transfer": "select count(*) from station_edges where edge_type='TRANSFER'",
    "edges_adjacent": "select count(*) from station_edges where edge_type='ADJACENT'",
    "elevators_real_sample": "select count(*) from elevators where source='OA-15994'",
    "elevator_status_real_sample": "select count(*) from elevator_status where source='OA-15994'",
    "elevators_synthetic": f"select count(*) from elevators where source='{SYNTHETIC_SOURCE}'",
    "elevator_status_synthetic": f"select count(*) from elevator_status where source='{SYNTHETIC_SOURCE}'",
}
_MAPPING_QUERY = (
    "select e.external_station_code, sl.line_id, s.station_name, s.station_id "
    "from station_external_ids e join station_lines sl on sl.station_line_id = e.station_line_id "
    "join stations s on s.station_id = sl.station_id where e.source = 'OA-12035' "
    "order by e.external_station_code"
)


def inspect_db(path: Path) -> dict:
    """DB를 읽기 전용(immutable)으로 열어 건수와 역 매핑을 읽는다. 파일을 바꾸지 않는다."""
    conn = _ro_connect(path)
    try:
        tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
        counts = {}
        for key, sql in _COUNT_QUERIES.items():
            table = re.search(r"from (\w+)", sql).group(1)
            counts[key] = conn.execute(sql).fetchone()[0] if table in tables else None
        mapping = [tuple(r) for r in conn.execute(_MAPPING_QUERY)] if "station_external_ids" in tables else []
        version = conn.execute("select version_num from alembic_version").fetchone() if "alembic_version" in tables else None
    finally:
        conn.close()
    return {"counts": counts, "mapping": mapping, "alembic": version[0] if version else None}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _alembic_upgrade(url: str) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


def _load_sample(db_url: str, sample: Path, fetched_at: str, log_path: Path) -> dict:
    """기존 수집 CLI의 파일 모드로 적재한다(임시 DB만 허용, 로그는 임시 경로)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = collect_elevators.main(
            ["--input-file", str(sample), "--fetched-at", fetched_at, "--database-url", db_url, "--apply", "--json"],
            log_path=log_path,
        )
    try:
        payload = json.loads(buffer.getvalue())
    except ValueError:
        payload = {}
    if code != 0:
        raise DemoBuildError(f"샘플 적재 실패(종료 코드 {code}): {payload.get('error')}")
    return payload


def _add_synthetic(session: Session, fetched_at: str) -> list[dict]:
    added = []
    next_id = (session.scalar(select(Elevator.elevator_id).order_by(Elevator.elevator_id.desc()).limit(1)) or 0) + 1
    for name, status in SYNTHETIC_SCENARIO:
        stations = list(session.scalars(select(Station).where(Station.station_name == name)))
        if len(stations) != 1:
            raise DemoBuildError(f"합성 시나리오의 역을 하나로 찾지 못했습니다: {name!r} ({len(stations)}건)")
        station = stations[0]
        session.add(Elevator(
            elevator_id=next_id, station_id=station.station_id, source=SYNTHETIC_SOURCE,
            external_station_code=f"DEMO-{station.station_id}",
            elevator_name=f"{SYNTHETIC_PREFIX} {name} 시연용 엘리베이터",
            location_description=SYNTHETIC_NOTE, operation_section=None,
        ))
        session.flush()
        session.add(ElevatorStatus(
            elevator_id=next_id, status=status, source_status_text=None, observed_at=None,
            fetched_at=fetched_at, source=SYNTHETIC_SOURCE,
        ))
        added.append({"station": name, "station_id": station.station_id, "elevator_id": next_id, "status": status})
        next_id += 1
    session.commit()
    return added


def build_demo_db(
    out_dir: Path,
    *,
    sample: Path = DEFAULT_SAMPLE,
    sample_fetched_at: str | None = None,
    include_provisional_adjacent: bool = False,
    synthetic: bool = True,
    seed_dir: Path = DEFAULT_SEED_DIR,
    source_db: Path | None = None,
    now: datetime | None = None,
) -> DemoResult:
    out = ensure_safe_output_dir(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    db_path = out / DEMO_DB_NAME
    url = f"sqlite:///{db_path}"
    stamp = sample_fetched_at or default_fetched_at(sample)

    _alembic_upgrade(url)
    engine = make_engine(url)
    synthetic_rows: list[dict] = []
    try:
        with Session(engine) as session:
            rows, links, review = read_seed_dir(seed_dir)
            load_seed(session, rows, links, review)
            load_edges(session, read_verified_edges(seed_dir))
            if include_provisional_adjacent:
                load_edges(session, read_provisional_edges(seed_dir), allow_provisional=True)
    finally:
        engine.dispose()

    sample_report = _load_sample(url, sample, stamp, out / "ingestion_issues.jsonl")

    synthetic_stamp = to_utc_iso(now or utc_now())
    if synthetic:
        engine = make_engine(url)
        try:
            with Session(engine) as session:
                synthetic_rows = _add_synthetic(session, synthetic_stamp)
        finally:
            engine.dispose()

    demo = inspect_db(db_path)
    source_info = None
    if source_db is not None and source_db.exists():
        src = inspect_db(source_db)
        source_info = {
            "path_name": source_db.name,
            "sha256": _sha256(source_db),
            "alembic": src["alembic"],
            "counts": {k: src["counts"][k] for k in ("stations", "station_lines")},
            "station_mapping_identical_to_demo": src["mapping"] == demo["mapping"],
        }

    manifest = {
        "kind": "DEMO",
        "warning": "해커톤 시연용 임시 DB. 운영 데이터가 아니다. data/dev.db에 적재하지 않는다.",
        "counts": demo["counts"],
        "alembic": demo["alembic"],
        "edges": {
            "transfer_verified_official": demo["counts"]["edges_transfer"],
            "adjacent_provisional_loaded": demo["counts"]["edges_adjacent"] if include_provisional_adjacent else 0,
            "note": "ADJACENT는 PROVISIONAL(공식 인접 증명 없음)이며 데모 DB에만 명시적 옵션으로 적재했다.",
        },
        "elevator_sample": {
            "file": sample.name,
            "sha256": _sha256(sample),
            "observed_at": None,
            "fetched_at": stamp,
            "fetched_at_meaning": "파일명 날짜의 00:00:00Z 하한값. 실제 수집 시각이 아님",
            "ingest_counts": sample_report.get("counts"),
            "disclaimer": SAMPLE_DISCLAIMER,
        },
        "synthetic": {
            "enabled": synthetic,
            "source": SYNTHETIC_SOURCE,
            "name_prefix": SYNTHETIC_PREFIX,
            "fetched_at": synthetic_stamp if synthetic else None,
            "rows": synthetic_rows,
            "note": "실제 샘플과 별도 행. 생성 시각 기준이며 TTL 이후 STALE이 된다. 실제 상태가 아님",
        },
        "ttl_policy": "기존 TTL(기본 120분)을 우회하지 않는다.",
        "source_db": source_info,
    }
    (out / "demo_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return DemoResult(out_dir=out, db_path=db_path, manifest=manifest)
