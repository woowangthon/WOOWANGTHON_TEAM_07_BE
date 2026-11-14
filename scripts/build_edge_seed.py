"""역 간선 시드(station_edges)를 등급별로 만든다. DB에 쓰지 않는다.

검증 등급
- VERIFIED    공식 출처가 관계를 직접 증명한 간선. 현재는 OA-13290 환승 기록으로 확인한 TRANSFER뿐이다.
- PROVISIONAL 프론트 순서와 공식 역번호 연속성이 일치하는 ADJACENT. 공식 인접역 자료로 증명되지 않았다.
              기본 로더는 적재하지 않는다.
- PENDING     그 밖의 모든 후보(매핑 실패, 역번호 불연속, 순환 연결 등). 적재하지 않는다.

입력
- 프론트 목업 JS (--mock-js): window.MOCK의 lines[].segments, lines[].loop, stations(이름·노선)만 읽는다.
  프론트 파일은 읽기 전용이고 저장소에 두지 않는다. JS를 실행하지 않고 텍스트로 파싱한다.
  elevators, alternativeTips, demoScenarios, 좌표(x, y)는 읽지도 쓰지도 않는다.
- data/seed (--seed-dir): stations_oa12035.csv, station_review.csv, transfer_evidence_oa13290.csv

출력 (--out-dir, 기본 data/seed)
- station_edges_verified.csv     VERIFIED(TRANSFER)만. 기본 로더가 읽는 유일한 파일이다.
- station_edges_provisional.csv  PROVISIONAL(ADJACENT). 기본 로더는 읽지 않고, 로더는 공식 근거 없는 ADJACENT를 거부한다.
- station_edges_review.csv       PENDING, 매핑 실패, 역명 변형 등 사람이 검토할 목록. 적재하지 않는다.
- station_edges_manifest.json  입력 해시, 건수, 정책

검증 규칙
[ADJACENT] 아래를 모두 만족하면 PROVISIONAL이다(VERIFIED가 아니다). 하나라도 어긋나면 PENDING이다.
  1. 프론트 segments에서 연속한 두 역이다(본선과 지선은 구분해 기록한다).
  2. 두 역이 같은 노선의 백엔드 역(OA-12035 코드) 하나로 유일하게 매핑된다.
     역명만으로 노선을 넘어 병합하지 않는다. 매핑 근거는 (a) 같은 노선 안 이름 일치,
     (b) 괄호 부연·'역' 접미사를 뺀 이름 일치, (c) 확정된 환승 그룹의 표시명 일치 중 하나다.
     프론트가 그 역의 노선에 해당 노선을 적지 않았거나, 한 백엔드 역에 프론트 역이 둘 이상
     매핑되면 실패다.
  3. 두 역의 공식 역번호(OA-12035)가 같은 노선에서 1 차이로 연속한다.
     프론트 순서와 독립된 교차 검증이지만, 역번호가 인접역 관계를 공식적으로 정의한다는 근거는 없다.
     공식 인접역 자료(역간거리 등)로 증명하기 전까지 PROVISIONAL이다.
  4. 순환선(loop=true)의 마지막-첫 역 연결은 공식 근거가 없어 PENDING이다.
[TRANSFER] 이미 확정된 OA-13290 환승 그룹(station_review.csv CONFIRMED)의 모든 쌍만 VERIFIED다.
  양방향 공식 기록이 있어야 한다. 프론트의 환승 표기는 근거로 쓰지 않는다.

방향 정책: 한 쌍은 한 행이다. from은 역번호가 작은 쪽이다. 소비하는 쪽은 무방향으로 취급한다.

사용: python scripts/build_edge_seed.py --mock-js ../mock-data.js
"""

import argparse
import collections
import csv
import hashlib
import itertools
import json
import re
import sys
from pathlib import Path

SOURCE_ADJACENT = "OA-12035+FE-MOCK"  # PROVISIONAL 전용. 공식 인접 증명이 아니다
SOURCE_TRANSFER = "OA-13290"
LINES = tuple("12345678")
VERIFIED_HEADER = ["edge_type", "from_code", "from_line", "to_code", "to_line", "source", "evidence"]
REVIEW_HEADER = ["review_type", "line", "ref", "status", "reason"]


class BuildError(Exception):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------- 프론트 목업 파싱 (lines, stations만) ----------

_LINE_RE = re.compile(
    r'"(\d)": \{\s*id: "\d", name: "[^"]+".*?loop: (true|false),\s*segments: \[(.*?)\n      \],', re.S
)
_STATION_RE = re.compile(
    r'"([^"]+)": \{ id: "[^"]+", name: "([^"]+)", x: [\d.\-]+, y: [\d.\-]+, lines: \[([^\]]*)\]'
)


def parse_mock(text: str) -> tuple[dict, dict]:
    lines: dict[str, dict] = {}
    for m in _LINE_RE.finditer(text):
        segments = [re.findall(r'"([^"]+)"', raw) for raw in re.findall(r"\[([^\[\]]*)\]", m.group(3))]
        lines[m.group(1)] = {"loop": m.group(2) == "true", "segments": segments}
    sm = re.search(r"var stations = \{(.*?)\n  \};", text, re.S)
    if not sm:
        raise BuildError("stations 블록을 찾을 수 없습니다.")
    stations = {
        m.group(1): {"name": m.group(2), "lines": re.findall(r'"(\d)"', m.group(3))}
        for m in _STATION_RE.finditer(sm.group(1))
    }
    if set(lines) != set(LINES):
        raise BuildError(f"1~8호선 segments를 모두 읽지 못했습니다: {sorted(lines)}")
    used = {i for v in lines.values() for seg in v["segments"] for i in seg}
    missing = used - set(stations)
    if missing:
        raise BuildError(f"segments의 역이 stations에 없습니다: {sorted(missing)[:5]}")
    return lines, stations


# ---------- 매핑 ----------


def normalize(name: str) -> str:
    name = re.sub(r"\(.*?\)", "", name).replace(" ", "")
    return name[:-1] if name.endswith("역") and len(name) > 1 else name


def map_stations(lines, stations, seed_rows, group_display):
    """(노선, 프론트 id) -> (코드, 근거) 와 실패 목록. 같은 노선 안에서만 찾는다."""
    by_line = collections.defaultdict(list)
    for r in seed_rows:
        by_line[r["line_id"]].append(r)
    mapping: dict[tuple[str, str], tuple[str, str]] = {}
    failures: list[tuple[str, str, str, str]] = []
    for line, spec in lines.items():
        for fe_id in dict.fromkeys(i for seg in spec["segments"] for i in seg):
            name = stations[fe_id]["name"]
            if line not in stations[fe_id]["lines"]:
                failures.append((line, fe_id, name, "FE_LINE_INCONSISTENT"))
                continue
            rows = by_line[line]
            exact = [r for r in rows if r["name"] == name]
            if len(exact) == 1:
                mapping[(line, fe_id)] = (exact[0]["code"], "exact")
                continue
            if len(exact) > 1:
                failures.append((line, fe_id, name, "AMBIGUOUS_EXACT"))
                continue
            norm = [r for r in rows if normalize(r["name"]) == normalize(name)]
            if len(norm) == 1:
                mapping[(line, fe_id)] = (norm[0]["code"], "normalized")
                continue
            if len(norm) > 1:
                failures.append((line, fe_id, name, "AMBIGUOUS_NORMALIZED"))
                continue
            grouped = [
                r for r in rows if group_display.get(r["code"]) == name
            ]
            if len(grouped) == 1:
                mapping[(line, fe_id)] = (grouped[0]["code"], "confirmed_group")
                continue
            failures.append((line, fe_id, name, "NOT_IN_BACKEND_SEED"))
    # 한 백엔드 역에 프론트 역이 둘 이상 매핑되면 둘 다 실패로 돌린다.
    owners = collections.defaultdict(list)
    for key, (code, _) in mapping.items():
        owners[code].append(key)
    for code, keys in owners.items():
        if len(keys) > 1:
            for key in keys:
                line, fe_id = key
                failures.append((line, fe_id, stations[fe_id]["name"], "DUPLICATE_MAPPING"))
                del mapping[key]
    return mapping, failures


# ---------- 간선 ----------


def _ordered(code_a, line_a, code_b, line_b):
    return (code_a, line_a, code_b, line_b) if code_a <= code_b else (code_b, line_b, code_a, line_a)


def build_adjacent(lines, stations, mapping, failed_keys):
    verified: dict[tuple, str] = {}
    pending: list[tuple[str, str, str, str]] = []
    seen_pairs: set[frozenset] = set()
    for line, spec in lines.items():
        for index, seg in enumerate(spec["segments"]):
            kind = "MAIN" if index == 0 else f"BRANCH{index}"
            pairs = list(zip(seg, seg[1:]))
            closure = spec["loop"] and index == 0 and len(seg) > 2
            for a, b in pairs:
                key = frozenset({(line, a), (line, b)})
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                ref = f"{a}>{b}"
                ma, mb = mapping.get((line, a)), mapping.get((line, b))
                if ma is None or mb is None:
                    who = [i for i, m in ((a, ma), (b, mb)) if m is None]
                    pending.append((line, ref, "PENDING", f"{kind}: 매핑 실패({','.join(who)})"))
                    continue
                gap = int(mb[0]) - int(ma[0])
                if abs(gap) != 1:
                    pending.append(
                        (line, ref, "PENDING",
                         f"{kind}: 공식 역번호가 연속하지 않음({ma[0]}→{mb[0]}). 프론트 순서를 공식 근거로 확인하지 못함")
                    )
                    continue
                edge = _ordered(ma[0], line, mb[0], line)
                verified[edge] = kind
            if closure:
                a, b = seg[-1], seg[0]
                pending.append(
                    (line, f"{a}>{b}", "PENDING",
                     f"{kind}: 순환 연결. 공식 근거 없음(프론트 loop 플래그뿐)")
                )
    return verified, pending


def build_transfers(confirmed_groups, evidence_rows, code_line):
    """확정 그룹의 모든 쌍. 양방향 공식 기록이 없으면 BuildError."""
    have = {(r["from_code"], r["to_code"]) for r in evidence_rows}
    edges = []
    for codes in confirmed_groups:
        for a, b in itertools.combinations(sorted(codes), 2):
            if (a, b) not in have or (b, a) not in have:
                raise BuildError(f"환승 공식 기록이 양방향으로 없습니다: {a}, {b}")
            if code_line[a] == code_line[b]:
                raise BuildError(f"같은 노선의 환승 쌍입니다: {a}, {b}")
            edges.append((a, code_line[a], b, code_line[b]))
    return edges


# ---------- 파일 읽기 ----------


def _read(path: Path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_seed(seed_dir: Path):
    seed_rows = [
        {"code": r["external_station_code"], "line_id": r["line_id"], "name": r["station_name"]}
        for r in _read(seed_dir / "stations_oa12035.csv")
    ]
    confirmed, display = [], {}
    for r in _read(seed_dir / "station_review.csv"):
        if r["review_type"] == "TRANSFER_CANDIDATE" and r["status"] == "CONFIRMED":
            codes = tuple(sorted(r["codes"].split("|")))
            confirmed.append(codes)
            for c in codes:
                display[c] = r["proposed_station_name"]
    evidence = _read(seed_dir / "transfer_evidence_oa13290.csv")
    return seed_rows, confirmed, display, evidence


# ---------- 조립 ----------


def build(mock_text: str, seed_dir: Path):
    lines, stations = parse_mock(mock_text)
    seed_rows, confirmed, display, evidence = read_seed(seed_dir)
    code_line = {r["code"]: r["line_id"] for r in seed_rows}
    mapping, failures = map_stations(lines, stations, seed_rows, display)
    failed = {(line, fe_id) for line, fe_id, _, _ in failures}
    adjacent, pending = build_adjacent(lines, stations, mapping, failed)
    transfers = build_transfers(confirmed, evidence, code_line)

    provisional_rows = [
        ["ADJACENT", a, la, b, lb, SOURCE_ADJACENT, f"FE_SEGMENT_{kind}+CODE_SEQUENCE"]
        for (a, la, b, lb), kind in sorted(adjacent.items())
    ]
    verified_rows = [
        ["TRANSFER", a, la, b, lb, SOURCE_TRANSFER, "CONFIRMED_GROUP"]
        for a, la, b, lb in sorted(transfers)
    ]

    review_rows = []
    for line, fe_id, name, reason in sorted(failures):
        review_rows.append(["MAPPING_FAILED", line, f"{fe_id}({name})", "PENDING", reason])
    for (line, fe_id), (code, basis) in sorted(mapping.items()):
        if basis != "exact":
            review_rows.append(
                ["NAME_VARIANT", line, f"{fe_id}({stations[fe_id]['name']})", "ACCEPTED_BY_LINE_AND_CODE",
                 f"백엔드 {code} 와 {basis} 일치. 간선은 역번호 연속성으로 따로 검증"]
            )
    for line, ref, status, reason in pending:
        review_rows.append(["ADJACENT_PENDING", line, ref, status, reason])

    # 프론트가 환승이라고 적었지만 확정 그룹으로 확인되지 않은 역(간선을 만들지 않는다)
    group_of = {c: g for g in confirmed for c in g}
    for fe_id, st in sorted(stations.items()):
        if len(st["lines"]) < 2:
            continue
        codes = {mapping[(ln, fe_id)][0] for ln in st["lines"] if (ln, fe_id) in mapping}
        groups = {group_of.get(c) for c in codes}
        if len(codes) >= 2 and (None in groups or len(groups) > 1):
            review_rows.append(
                ["TRANSFER_UNCONFIRMED", "-", f"{fe_id}({st['name']})", "PENDING",
                 "프론트 환승 표기를 OA-13290 확정 그룹으로 확인하지 못함. 환승 간선을 만들지 않음"]
            )

    # 백엔드 역번호는 연속하지만 간선이 없는 쌍(프론트 누락 가능성)
    present = {(a, b) for (a, _, b, _) in adjacent}
    by_line = collections.defaultdict(list)
    for r in seed_rows:
        by_line[r["line_id"]].append(r["code"])
    for line, codes in sorted(by_line.items()):
        codes.sort()
        for a, b in zip(codes, codes[1:]):
            if int(b) - int(a) == 1 and (a, b) not in present:
                review_rows.append(
                    ["BACKEND_CODE_NEIGHBOR_WITHOUT_EDGE", line, f"{a}-{b}", "PENDING",
                     "역번호는 연속하지만 검증된 간선이 없음(프론트 누락 또는 매핑 실패)"]
                )
    return verified_rows, provisional_rows, review_rows, {
        "mapping_success": len(mapping),
        "mapping_failed": len(failures),
        "mapping_failed_by_reason": dict(collections.Counter(f[3] for f in failures)),
        "name_variants_accepted": sum(1 for b in mapping.values() if b[1] != "exact"),
        "adjacent_verified": 0,  # 공식 인접 증명 출처가 없어 0이다
        "adjacent_provisional": len(adjacent),
        "adjacent_pending": len(pending),
        "transfer_verified": len(transfers),
        "confirmed_transfer_groups": len(confirmed),
    }


def write_csv(path: Path, header, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mock-js", required=True, type=Path)
    parser.add_argument("--seed-dir", type=Path, default=Path("data/seed"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/seed"))
    args = parser.parse_args()
    try:
        verified, provisional, review, stats = build(args.mock_js.read_text(encoding="utf-8"), args.seed_dir)
    except (BuildError, OSError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.out_dir / "station_edges_verified.csv", VERIFIED_HEADER, verified)
    write_csv(args.out_dir / "station_edges_provisional.csv", VERIFIED_HEADER, provisional)
    write_csv(args.out_dir / "station_edges_review.csv", REVIEW_HEADER, review)
    manifest = {
        "frontend_mock": {
            "file_name": args.mock_js.name,
            "sha256": sha256(args.mock_js),
            "used_fields": ["lines[].segments", "lines[].loop", "stations[].name", "stations[].lines"],
            "not_used": ["elevators", "alternativeTips", "demoScenarios", "x/y coordinates"],
        },
        "grades": {
            "VERIFIED": "공식 출처가 관계를 직접 증명(OA-13290 환승 기록). TRANSFER만 해당",
            "PROVISIONAL": "프론트 순서와 공식 역번호 연속성이 일치하는 ADJACENT. 공식 인접 증명 아님. 기본 로더 적재 제외",
            "PENDING": "그 밖의 후보. 적재 안 함",
        },
        "adjacent_source_provisional": SOURCE_ADJACENT,
        "transfer_source": SOURCE_TRANSFER,
        "direction_policy": "한 쌍당 한 행(from은 역번호가 작은 쪽). 소비하는 쪽은 무방향으로 취급",
        "adjacent_evidence_limit": "공식 인접역 자료 없음. 역번호 연속성은 인접 관계의 공식 증명이 아니다",
        "stats": stats,
        "review_rows": len(review),
    }
    (args.out_dir / "station_edges_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
