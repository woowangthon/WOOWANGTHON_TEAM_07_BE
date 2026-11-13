"""OA-12035 원본 CSV에서 1~8호선 시드 파일을 만들고, 공식 환승 기록으로 환승 그룹을 검증한다.

입력
- OA-12035 원본 CSV (CP949): 연번, 역번호, 호선, 역명, 역전화번호, 도로명주소, 지번주소
- OA-15994 샘플 JSON (STN_CD 대조용)
- OA-13290 원본 CSV (CP949): 서울교통공사 환승역거리 소요시간 정보 (환승 여부의 공식 근거)

출력 (--out-dir, 기본 data/seed)
- stations_oa12035.csv           1~8호선 행. 코드는 4자리 0 패딩 문자열
- external_code_links.csv        OA-15994 STN_CD 중 OA-12035 역번호와 일치가 확인된 코드
- station_review.csv             REVIEW 목록. 공식 근거가 확인된 환승 그룹만 CONFIRMED
- transfer_evidence_oa13290.csv  CONFIRMED 그룹의 공식 기록(방향별 한 행)
- manifest.json                  출처, 행 수, 해시, 커버리지

환승 확정 규칙 (역명이나 주소가 같다는 이유만으로는 확정하지 않는다)
1. 공식 기록(OA-13290)의 한 행은 "A호선 X역에서 B호선으로 환승"이다.
   X를 시드의 A호선 역 하나로 찾을 때, 같은 노선 안에서 정확히 일치하거나 괄호 부연만 뺀
   이름이 일치하는 역이 유일해야 한다. 그렇지 않으면 해석하지 않는다.
2. 반대 방향 기록(B호선 Y역에서 A호선으로 환승)도 있어야 하고, 두 기록의 환승거리와
   환승소요시간이 같아야 한다. 양쪽 역이 시드에서 각각 하나로 해석되어야 한다.
3. 위 쌍으로 이어지는 역들을 한 그룹으로 보고, 그룹 안의 모든 쌍이 확인되고 노선이
   서로 달라야 CONFIRMED로 한다. 하나라도 빠지면 CONFIRMED로 하지 않는다.
4. 위에서 확인되지 않은 후보(역명 동일 후보 등)는 PENDING으로 둔다.
5. 예외: MANUAL_CONFIRMATIONS에 명시한 그룹만 수동 확정한다. 일반 규칙이 아니며, 그룹마다
   (a) 공식 환승 기록이 양방향으로 있고 거리·시간이 같으며 (b) OA-12035 지번주소에 해당 역명과
   노선이 표기되어 있음을 스크립트가 직접 검증한다. 검증에 실패하면 생성을 중단한다.

코드나 노선은 추정하지 않는다. 원본 값을 그대로 옮기고 4자리 0 패딩과 앞뒤 공백 제거만 한다.

사용: python scripts/build_station_seed.py --source-csv <OA-12035.csv> \\
        --oa15994-sample <샘플.json> --transfer-csv <OA-13290.csv>
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

SOURCE_ID = "OA-12035"
EXTERNAL_SOURCE_15994 = "OA-15994"
LINES = {"1", "2", "3", "4", "5", "6", "7", "8"}
SOURCE_URL = "https://data.seoul.go.kr/dataList/OA-12035/S/1/datasetView.do"
TRANSFER_URL = "https://data.seoul.go.kr/dataList/OA-13290/S/1/datasetView.do"
MANUAL_APPROVED_ON = "2026-11-13"
# 수동 확정 목록. 역명이 같다는 이유로 병합하는 규칙이 아니라, 명시한 코드 묶음만 검증 후 확정한다.
# 키: 코드 묶음(정렬), 값: 표시명. 서울역은 시드 역명이 '서울'이라 규칙 1로 연결되지 않는다.
MANUAL_CONFIRMATIONS: dict[tuple[str, ...], str] = {
    ("0150", "0426"): "서울역",
}
# 사용자가 지정한 표시명(2026-11-13). 병합 근거가 아니라 표시용이다.
DISPLAY_NAME_OVERRIDES: dict[tuple[str, ...], str] = {
    ("0432", "2738"): "총신대입구(이수)",
}
EVIDENCE_HEADER = [
    "group_codes", "from_code", "from_line", "official_station_name", "name_match",
    "to_code", "to_line", "distance_m", "transfer_time",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_cp949_csv(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.read_bytes().decode("cp949").splitlines()))


def strip_address_suffix(address: str) -> str:
    """지번주소 끝의 '○○역(n호선)' 표기를 떼어 위치 부분만 남긴다. 후보 근거 표시용이다."""
    return re.sub(r"\s*\S*역\s*\(\d+호선\)\s*$", "", address).strip()


def strip_line_suffix(name: str) -> str:
    return re.sub(r"\(\d+\)$", "", name).strip()


def base_name(name: str) -> str:
    """끝의 괄호 부연(예: '교대(법원, 검찰청)')을 뺀 이름. 비교용이며 병합 근거가 아니다."""
    return re.sub(r"\([^)]*\)\s*$", "", name).strip()


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def resolve_station(by_line, line: str, name: str):
    """공식 기록의 (호선, 환승역명)을 시드 코드 하나로 찾는다. 유일할 때만 인정한다."""
    exact = [r["code"] for r in by_line[line] if r["name"] == name]
    if len(exact) == 1:
        return exact[0], "exact"
    if exact:
        return None, "ambiguous"
    based = [r["code"] for r in by_line[line] if base_name(r["name"]) == base_name(name)]
    if len(based) == 1:
        return based[0], "base"
    return None, "ambiguous" if based else "none"


def verify_transfers(seed, transfer_rows):
    """공식 환승 기록으로 확인되는 그룹을 만든다.

    반환: (groups, evidence_rows, unresolved)
      groups: [(codes_tuple, lines_tuple)] 완전하고 노선이 모두 다른 그룹만
      evidence_rows: 증거 CSV 행
      unresolved: 해석하지 못한 공식 기록 (호선, 역명, 환승노선, 사유)
    """
    by_line = collections.defaultdict(list)
    for s in seed:
        by_line[s["line_id"]].append({"code": s["code"], "name": s["name"]})
    line_of = {s["code"]: s["line_id"] for s in seed}
    rows = [
        r for r in transfer_rows
        if re.fullmatch(r"[1-8]호선", r["환승노선"]) and r["호선"] != r["환승노선"][0]
    ]
    pairs: dict[frozenset, dict] = {}
    unresolved = []
    for r in rows:
        a_line, b_line, name = r["호선"], r["환승노선"][0], r["환승역명"].strip()
        a, a_match = resolve_station(by_line, a_line, name)
        if a is None:
            unresolved.append((a_line, name, r["환승노선"], "이 노선의 시드 역과 해석되지 않음"))
            continue
        found = None
        for x in rows:
            if x["호선"] != b_line or x["환승노선"][0] != a_line:
                continue
            b, b_match = resolve_station(by_line, b_line, x["환승역명"].strip())
            if b and x["환승거리"] == r["환승거리"] and x["환승소요시간"] == r["환승소요시간"]:
                found = (b, b_match, x)
                break
        if found is None:
            unresolved.append((a_line, name, r["환승노선"], "상대 노선의 시드 역 또는 일치하는 반대 방향 기록이 없음"))
            continue
        b, b_match, x = found
        pairs[frozenset((a, b))] = {
            a: (name, a_match, r["환승거리"], r["환승소요시간"]),
            b: (x["환승역명"].strip(), b_match, x["환승거리"], x["환승소요시간"]),
        }

    parent: dict[str, str] = {}

    def find(c: str) -> str:
        parent.setdefault(c, c)
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c

    for p in pairs:
        a, b = tuple(p)
        parent[find(a)] = find(b)
    comps: dict[str, set] = collections.defaultdict(set)
    for p in pairs:
        for c in p:
            comps[find(c)].add(c)

    groups, evidence = [], []
    for members in comps.values():
        codes = tuple(sorted(members))
        lines = tuple(line_of[c] for c in codes)
        complete = all(frozenset(p) in pairs for p in itertools.combinations(codes, 2))
        if not complete or len(set(lines)) != len(lines):
            continue
        groups.append((codes, lines))
        key = "|".join(codes)
        for p in itertools.combinations(codes, 2):
            info = pairs[frozenset(p)]
            for frm, to in (p, p[::-1]):
                name, match, dist, tm = info[frm]
                evidence.append([key, frm, line_of[frm], name, match, to, line_of[to], dist, tm])
    groups.sort()
    evidence.sort(key=lambda e: (e[0], e[1], e[5]))
    return groups, evidence, unresolved


def verify_manual_group(codes, display, seed_by_code, transfer_rows):
    """수동 확정 그룹의 근거를 검증한다. 반환: (증거 CSV 행, 비고용 요약). 실패하면 ValueError."""
    lines = [seed_by_code[c]["line_id"] for c in codes]
    if len(set(lines)) != len(lines):
        raise ValueError(f"수동 확정 그룹에 같은 노선 코드가 있습니다: {codes}")
    evidence, facts = [], []
    for a, b in itertools.combinations(codes, 2):
        la, lb = seed_by_code[a]["line_id"], seed_by_code[b]["line_id"]
        fwd = [r for r in transfer_rows if r["호선"] == la and r["환승노선"] == f"{lb}호선" and r["환승역명"].strip() == display]
        rev = [r for r in transfer_rows if r["호선"] == lb and r["환승노선"] == f"{la}호선" and r["환승역명"].strip() == display]
        if len(fwd) != 1 or len(rev) != 1:
            raise ValueError(f"공식 환승 기록이 양방향으로 하나씩 없습니다: {display} {la}호선↔{lb}호선")
        if (fwd[0]["환승거리"], fwd[0]["환승소요시간"]) != (rev[0]["환승거리"], rev[0]["환승소요시간"]):
            raise ValueError(f"양방향 기록의 거리·시간이 다릅니다: {display} {la}호선↔{lb}호선")
        for frm, to, row in ((a, b, fwd[0]), (b, a, rev[0])):
            evidence.append([
                "|".join(codes), frm, seed_by_code[frm]["line_id"], display, "manual",
                to, seed_by_code[to]["line_id"], row["환승거리"], row["환승소요시간"],
            ])
        facts.append(f"{la}호선↔{lb}호선 {fwd[0]['환승거리']}m {fwd[0]['환승소요시간']}")
    for c in codes:
        s = seed_by_code[c]
        if not re.search(rf"{re.escape(display)}\s*\({s['line_id']}호선\)", s["full_addr"]):
            raise ValueError(f"OA-12035 지번주소에 '{display}({s['line_id']}호선)' 표기가 없습니다: {c} {s['full_addr']!r}")
    return evidence, "; ".join(facts)


def display_name(codes, seed_by_code):
    """CONFIRMED 그룹의 표시명을 정한다. 역을 합치는 근거가 아니라 표시용이다."""
    names = [seed_by_code[c]["name"] for c in codes]
    if len(set(names)) == 1:
        return names[0], ""
    bases = {base_name(n) for n in names}
    if len(bases) == 1:
        return bases.pop(), "노선별 역명 표기가 달라 공통 이름(괄호 부연 제외)을 표시명으로 지정"
    first = min(codes, key=lambda c: (seed_by_code[c]["line_id"], c))
    return seed_by_code[first]["name"], (
        "노선별 역명이 서로 다르고 공통 이름이 없어 가장 낮은 노선의 역명을 표시명으로 임시 지정(표시명 검토 필요): "
        + " / ".join(f"{seed_by_code[c]['line_id']}호선 {seed_by_code[c]['name']}" for c in codes)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source-csv", required=True, type=Path)
    parser.add_argument("--oa15994-sample", required=True, type=Path)
    parser.add_argument("--transfer-csv", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("data/seed"))
    args = parser.parse_args()

    source_rows = read_cp949_csv(args.source_csv)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    seed = []  # dict: code, line_id, name, serial, raw_name, addr
    for r in source_rows:
        if r["호선"] not in LINES:
            continue
        code = r["역번호"].strip().zfill(4)
        if not re.fullmatch(r"\d{4}", code):
            print(f"잘못된 역번호: {r['역번호']!r}", file=sys.stderr)
            return 1
        seed.append({
            "code": code, "line_id": r["호선"], "name": r["역명"].strip(),
            "serial": r["연번"], "raw_name": r["역명"], "addr": strip_address_suffix(r["지번주소"]),
            "full_addr": r["지번주소"],
        })
    if len({s["code"] for s in seed}) != len(seed):
        print("역번호가 중복되었습니다.", file=sys.stderr)
        return 1
    seed_by_code = {s["code"]: s for s in seed}

    write_csv(
        out / "stations_oa12035.csv",
        ["external_station_code", "line_id", "station_name", "source_serial"],
        [[s["code"], s["line_id"], s["name"], s["serial"]] for s in seed],
    )

    # OA-15994 STN_CD 대조: 코드가 시드에 있는 것만 연결 대상으로 기록한다.
    sample = json.loads(args.oa15994_sample.read_text(encoding="utf-8"))["row"]
    names_15994: dict[str, set[str]] = collections.defaultdict(set)
    for r in sample:
        names_15994[r["STN_CD"]].add(r["STN_NM"])
    linked = sorted(c for c in names_15994 if c in seed_by_code)
    unlinked_15994 = sorted(c for c in names_15994 if c not in seed_by_code)
    if unlinked_15994:
        print(f"시드에 없는 OA-15994 코드: {unlinked_15994}", file=sys.stderr)
    basis = "OA-15994 STN_CD(2026-11-13 샘플) == OA-12035 역번호(4자리 패딩)"
    write_csv(
        out / "external_code_links.csv",
        ["source", "external_station_code", "basis"],
        [[EXTERNAL_SOURCE_15994, c, basis] for c in linked],
    )

    # ---- 환승 그룹: 공식 기록으로 확인된 것만 CONFIRMED ----
    transfer_rows = read_cp949_csv(args.transfer_csv)
    confirmed, evidence, unresolved = verify_transfers(seed, transfer_rows)
    confirmed_sets = {codes: lines for codes, lines in confirmed}

    manual_notes: dict[tuple, str] = {}
    manual_names: dict[tuple, str] = {}
    for codes, display in MANUAL_CONFIRMATIONS.items():
        if any(set(codes) & set(c) for c in confirmed_sets):
            print(f"수동 확정 코드가 자동 확정 그룹과 겹칩니다: {codes}", file=sys.stderr)
            return 1
        try:
            manual_evidence, facts = verify_manual_group(codes, display, seed_by_code, transfer_rows)
        except ValueError as exc:
            print(f"수동 확정 근거 검증 실패: {exc}", file=sys.stderr)
            return 1
        evidence.extend(manual_evidence)
        confirmed_sets[codes] = tuple(seed_by_code[c]["line_id"] for c in codes)
        manual_names[codes] = display
        seed_names = " / ".join(f"{seed_by_code[c]['line_id']}호선 {seed_by_code[c]['name']}" for c in codes)
        manual_notes[codes] = (
            f"수동 확정({MANUAL_APPROVED_ON} 사용자 승인). 근거: (1) OA-13290(2025.03.31) 공식 환승 기록이 양방향으로 있고 "
            f"거리·시간이 같음({facts}) (2) OA-12035 지번주소에 '{display}(n호선)' 표기가 있음. "
            f"시드 역명({seed_names})과 공식 역명({display})이 달라 이름 규칙으로 연결되지 않아 이 그룹만 명시적으로 확정함. "
            "역명만으로 병합하는 일반 규칙이 아님. 노선별 시드 원본 역명은 stations_oa12035.csv에 그대로 보존됨. "
            "근거는 transfer_evidence_oa13290.csv(name_match=manual)."
        )
    evidence.sort(key=lambda e: (e[0], e[1], e[5]))
    write_csv(out / "transfer_evidence_oa13290.csv", EVIDENCE_HEADER, evidence)
    # 수동 확정으로 해석된 공식 기록은 해석 못 한 행 목록에서 뺀다.
    resolved_manual = {(seed_by_code[c]["line_id"], manual_names[codes]) for codes in manual_names for c in codes}
    unresolved = [u for u in unresolved if (u[0], u[1]) not in resolved_manual]

    # 역명·주소 기반 후보 (병합 근거가 아니라 점검 대상 목록)
    candidates: dict[tuple, str] = {}  # codes -> kind note
    by_name = collections.defaultdict(list)
    for s in seed:
        by_name[s["name"]].append(s)
    for name in sorted(by_name):
        group = by_name[name]
        if len({g["line_id"] for g in group}) < 2:
            continue
        same_addr = len({g["addr"] for g in group}) == 1
        candidates[tuple(sorted(g["code"] for g in group))] = (
            "역명 동일, 노선 상이. 지번주소 " + ("동일" if same_addr else "상이")
        )
    by_addr = collections.defaultdict(list)
    for s in seed:
        by_addr[s["addr"]].append(s)
    for addr in sorted(by_addr):
        group = by_addr[addr]
        if len({g["line_id"] for g in group}) >= 2 and len({g["name"] for g in group}) >= 2:
            candidates[tuple(sorted(g["code"] for g in group))] = (
                "지번주소 동일, 역명 상이(" + " / ".join(sorted(g["name"] for g in group)) + ")"
            )

    review: list[list[str]] = []
    pending_transfer = 0
    name_for_official: dict[tuple, str] = {}
    for codes, lines in sorted(confirmed_sets.items()):
        if codes in manual_notes:
            review.append(["TRANSFER_CANDIDATE", "|".join(codes), manual_names[codes], "CONFIRMED", manual_notes[codes]])
            continue
        name, name_note = display_name(codes, seed_by_code)
        if codes in DISPLAY_NAME_OVERRIDES:
            name = DISPLAY_NAME_OVERRIDES[codes]
            name_note = (
                f"통합 표시명은 사용자 지정({MANUAL_APPROVED_ON}). 노선별 원본 역명은 stations_oa12035.csv에 보존됨: "
                + " / ".join(f"{seed_by_code[c]['line_id']}호선 {seed_by_code[c]['name']}" for c in codes)
            )
        origin = "후보 목록(역명·주소 기반)과 일치" if codes in candidates else (
            "역명 동일 후보에 없던 그룹" if not any(set(codes) <= set(k) or set(k) <= set(codes) for k in candidates)
            else "역명 동일 후보를 확장·교체한 그룹"
        )
        matches = {e[4] for e in evidence if e[0] == "|".join(codes)}
        note = (
            f"OA-13290(2025.03.31) 공식 환승 기록으로 확인: {len(codes)}개 노선 "
            f"{len(list(itertools.combinations(codes, 2)))}쌍 모두 양방향 기록·거리·시간 일치. "
            f"역명 해석={'+'.join(sorted(matches))}. 근거는 transfer_evidence_oa13290.csv. {origin}."
        )
        if name_note:
            note += " " + name_note
        review.append(["TRANSFER_CANDIDATE", "|".join(codes), name, "CONFIRMED", note])

    for codes, kind in sorted(candidates.items()):
        if codes in confirmed_sets:
            continue
        if any(set(codes) <= set(c) for c in confirmed_sets):
            continue  # 더 큰 CONFIRMED 그룹으로 교체됨
        members = [seed_by_code[c] for c in codes]
        official_hint = []
        for a, b in itertools.permutations(members, 2):
            names = sorted({
                r["환승역명"].strip() for r in transfer_rows
                if r["호선"] == a["line_id"] and r["환승노선"] == f"{b['line_id']}호선"
            })
            line_seed_names = [s["name"] for s in seed if s["line_id"] == a["line_id"]]
            # 이미 이 노선의 시드 역으로 해석되는 공식 역명(다른 그룹의 근거)은 제외하고, 해석되지 않는 이름만 적는다.
            unmatched = [
                n for n in names
                if n not in line_seed_names and base_name(n) not in [base_name(x) for x in line_seed_names]
            ]
            if unmatched:
                official_hint.append(f"{a['line_id']}호선→{b['line_id']}호선 해석되지 않는 공식 역명={'/'.join(unmatched)}")
        note = kind + ". 자동 병합하지 않음"
        if official_hint:
            note += (
                ". 공식 환승 기록에 같은 노선 쌍의 환승이 있으나 공식 역명을 시드 역명으로 해석하지 못해 확정하지 않음("
                + "; ".join(official_hint) + "). 표기 연결을 사람이 확인해야 함"
            )
        review.append(["TRANSFER_CANDIDATE", "|".join(codes), "", "PENDING", note])
        pending_transfer += 1

    # 역명 차이: OA-15994 역명(괄호 노선 번호 제거)과 시드 역명이 다른 코드.
    pending_names = 0
    for code in linked:
        s = seed_by_code[code]
        name15994 = sorted(names_15994[code])[0]
        base = strip_line_suffix(name15994)
        if base == s["name"]:
            if s["raw_name"] != s["name"]:
                review.append([
                    "NAME_VARIANT", code, "", "ACCEPTED_BY_CODE",
                    f"원본 역명에 앞뒤 공백이 있었음({s['raw_name']!r}). 공백만 제거. OA-15994={name15994}",
                ])
            continue
        if s["name"].startswith(base + "("):
            status, kind = "ACCEPTED_BY_CODE", "시드 역명에 괄호 부연이 붙음"
        else:
            status, kind = "PENDING", "표기가 다름. 확인 필요"
            pending_names += 1
        review.append([
            "NAME_VARIANT", code, "", status,
            f"{kind}. OA-15994={name15994} / OA-12035={s['name']}",
        ])

    write_csv(
        out / "station_review.csv",
        ["review_type", "codes", "proposed_station_name", "status", "note"],
        review,
    )

    # 공식 기록 중 1~8호선 쌍인데 시드로 해석되지 않은 행 = 시드 커버리지 공백의 단서
    gap_lines = collections.Counter()
    gaps = []
    for ln, nm, to, reason in unresolved:
        gaps.append({"line": ln, "official_station_name": nm, "transfer_to": to, "reason": reason})
    for g in gaps:
        gap_lines[g["transfer_to"]] += 1

    per_line = collections.Counter(s["line_id"] for s in seed)
    manifest = {
        "source": {
            "dataset_id": SOURCE_ID,
            "title": "서울교통공사_역주소 및 전화번호",
            "url": SOURCE_URL,
            "file_name": args.source_csv.name,
            "file_sha256": sha256(args.source_csv),
            "encoding": "cp949",
            "retrieved_on": "2026-11-13",
            "base_date": "미확정: 페이지 본문은 2025년 8월 기준, 최신 파일명은 20250318, 검색 결과에는 2026년 2월 기준 표기",
            "license": "공공누리 1유형 (페이지 표기, 원문 대조 전)",
            "operator_scope": "서울교통공사",
        },
        "transfer_source": {
            "dataset_id": "OA-13290",
            "title": "서울교통공사_환승역거리 소요시간 정보",
            "url": TRANSFER_URL,
            "file_name": args.transfer_csv.name,
            "file_sha256": sha256(args.transfer_csv),
            "encoding": "cp949",
            "rows": len(transfer_rows),
            "retrieved_on": "2026-11-13",
            "base_date": "2025.03 기준 자료(페이지 설명), 데이터 갱신일 2025.03.31, 갱신주기 연간(페이지 표기)",
            "license": "공공누리 1유형 (검색 결과 표기, 원문 대조 전)",
            "note": "환승소요시간은 환승거리를 보행속도 1.2m/s로 나눈 추정치다(페이지 설명). 이 시드는 환승 여부 근거로만 쓰고 소요시간은 쓰지 않는다.",
        },
        "rows": {
            "source_total": len(source_rows),
            "seed_lines_1_to_8": len(seed),
            "excluded_line_9": len(source_rows) - len(seed),
            "per_line": {k: per_line[k] for k in sorted(per_line)},
        },
        "coverage": {
            "line_1": "PARTIAL",
            "line_1_note": f"1호선은 {per_line['1']}개 역만 포함한다. 나머지 구간은 별도 조사 대상이며 미확보다.",
            "lines_2_to_8": "UNVERIFIED_AGAINST_OFFICIAL_COUNTS",
            "statement": "1~8호선 전체를 확보했다는 뜻이 아니다.",
            "official_transfer_rows_not_resolvable_to_seed": gaps,
        },
        "oa15994_link": {
            "sample": args.oa15994_sample.name,
            "sample_sha256": sha256(args.oa15994_sample),
            "stn_cd_distinct": len(names_15994),
            "linked": len(linked),
            "not_in_seed": unlinked_15994,
            "basis": basis,
        },
        "review": {
            "transfer_confirmed_groups": len(confirmed_sets),
            "transfer_confirmed_manual": len(manual_notes),
            "transfer_candidates_pending": pending_transfer,
            "name_variant_pending": pending_names,
            "name_variant_accepted_by_code": sum(
                1 for r in review if r[0] == "NAME_VARIANT" and r[3] == "ACCEPTED_BY_CODE"
            ),
        },
        "generated_by": "scripts/build_station_seed.py",
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest["rows"], ensure_ascii=False))
    print(json.dumps(manifest["review"], ensure_ascii=False))
    print("확정 그룹 노선 수 분포:", dict(collections.Counter(len(c) for c in confirmed_sets)))
    print("해석 못 한 공식 기록:", len(gaps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
