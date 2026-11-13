"""OA-15994 서울교통공사 승강기 가동현황(SeoulMetroFaciInfo) 응답 파서.

순수 함수만 둔다. 네트워크, DB, 시계를 쓰지 않는다.

확인된 필드(docs/api-notes.md 3절)는 7개뿐이며 모두 문자열이다.
    STN_CD, STN_NM, ELVTR_NM, OPR_SEC, INSTL_PSTN, USE_YN, ELVTR_SE
이 모듈은 그 7개만 읽는다. 원본에 없는 값은 만들지 않는다.
- 설비 고유 ID가 없다. (STN_CD, ELVTR_NM)을 식별 기준으로 쓰고 중복을 오류로 본다.
- 원본 측정 시각(observed_at)이 없다. 이 모듈은 어떤 시각도 만들지 않는다.
  수집 시각(fetched_at)은 적재 단계에서 다룬다.
- ELVTR_SE가 EV인 행만 엘리베이터로 추출한다. ES, WL, MW 등은 제외한다.
- USE_YN은 `사용가능`, `보수중`만 해석한다. 그 밖의 값은 상태를 추정하지 않고 None(미확인)으로
  두며 원문을 보존한다. 임의로 가동중이나 가동 안됨으로 바꾸지 않는다.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

SERVICE_NAME = "SeoulMetroFaciInfo"
ELEVATOR_TYPE = "EV"

REQUIRED_FIELDS = ("STN_CD", "STN_NM", "ELVTR_NM", "OPR_SEC", "INSTL_PSTN", "USE_YN", "ELVTR_SE")
# 값이 비어 있으면 안 되는 필드. OPR_SEC, INSTL_PSTN은 elevators 모델에서 NULL을 허용한다.
NON_EMPTY_FIELDS = ("STN_CD", "STN_NM", "ELVTR_NM", "USE_YN", "ELVTR_SE")

# 확인된 상태 값만 명시적으로 해석한다. docs/api-contract.md 6.1절과 같다.
STATUS_BY_USE_YN = {"사용가능": "가동중", "보수중": "가동 안됨"}

# \d는 전각 숫자 같은 유니코드 숫자도 허용하므로 ASCII 숫자만 쓴다.
_STATION_CODE_PATTERN = re.compile(r"[0-9]{4}")


class Oa15994ParseError(Exception):
    """응답 구조나 값이 확인된 형식과 다를 때. 메시지에는 행 번호와 필드 이름만 쓴다."""


@dataclass(frozen=True)
class ElevatorRecord:
    """파싱된 엘리베이터 한 건. 원본 필드 외의 값은 없다."""

    station_code: str  # STN_CD (4자리)
    station_name: str  # STN_NM
    elevator_name: str  # ELVTR_NM, 같은 역 안에서 설비를 구분하는 값
    operation_section: str | None  # OPR_SEC (빈 값이면 None)
    location_description: str | None  # INSTL_PSTN (빈 값이면 None)
    source_status_text: str  # USE_YN 원문
    status: str | None  # 가동중 / 가동 안됨 / None(해석하지 않은 값)

    @property
    def key(self) -> tuple[str, str]:
        return (self.station_code, self.elevator_name)


@dataclass
class ParseResult:
    elevators: list[ElevatorRecord]
    excluded_by_type: Counter = field(default_factory=Counter)  # EV가 아닌 행의 ELVTR_SE별 건수
    total_rows: int = 0

    @property
    def unknown_status(self) -> list[ElevatorRecord]:
        return [e for e in self.elevators if e.status is None]

    @property
    def station_codes(self) -> set[str]:
        return {e.station_code for e in self.elevators}


def _validate_row(index: int, row: Any) -> dict[str, str]:
    if not isinstance(row, dict):
        raise Oa15994ParseError(f"row[{index}]: 객체가 아닙니다.")
    missing = [f for f in REQUIRED_FIELDS if f not in row]
    if missing:
        raise Oa15994ParseError(f"row[{index}]: 필드가 없습니다: {', '.join(missing)}")
    for name in REQUIRED_FIELDS:
        if not isinstance(row[name], str):
            raise Oa15994ParseError(f"row[{index}]: {name} 값이 문자열이 아닙니다.")
    empty = [f for f in NON_EMPTY_FIELDS if not row[f].strip()]
    if empty:
        raise Oa15994ParseError(f"row[{index}]: 빈 값입니다: {', '.join(empty)}")
    if not _STATION_CODE_PATTERN.fullmatch(row["STN_CD"]):
        raise Oa15994ParseError(f"row[{index}]: STN_CD가 4자리 숫자가 아닙니다.")
    return row


def _optional_text(value: str) -> str | None:
    return value if value.strip() else None


def parse_elevators(rows: list[dict]) -> ParseResult:
    """row 배열에서 EV 설비만 뽑는다.

    - 모든 행의 필수 필드를 검증한다. 하나라도 어긋나면 전체를 오류로 한다(일부만 쓰지 않는다).
    - EV 행의 (STN_CD, ELVTR_NM)이 중복되면 오류다.
    - 입력 순서를 유지한다.
    """
    if not isinstance(rows, list):
        raise Oa15994ParseError("row가 배열이 아닙니다.")

    elevators: list[ElevatorRecord] = []
    excluded: Counter = Counter()
    seen: dict[tuple[str, str], int] = {}
    for index, raw in enumerate(rows):
        row = _validate_row(index, raw)
        if row["ELVTR_SE"] != ELEVATOR_TYPE:
            excluded[row["ELVTR_SE"]] += 1
            continue
        key = (row["STN_CD"], row["ELVTR_NM"])
        if key in seen:
            raise Oa15994ParseError(
                f"row[{index}]: 식별자(STN_CD, ELVTR_NM)가 row[{seen[key]}]와 중복됩니다. STN_CD={row['STN_CD']}"
            )
        seen[key] = index
        elevators.append(
            ElevatorRecord(
                station_code=row["STN_CD"],
                station_name=row["STN_NM"],
                elevator_name=row["ELVTR_NM"],
                operation_section=_optional_text(row["OPR_SEC"]),
                location_description=_optional_text(row["INSTL_PSTN"]),
                source_status_text=row["USE_YN"],
                status=STATUS_BY_USE_YN.get(row["USE_YN"]),
            )
        )
    return ParseResult(elevators=elevators, excluded_by_type=excluded, total_rows=len(rows))


def load_sample_rows(document: dict) -> list[dict]:
    """저장된 샘플(docs/samples)의 구조에서 row 배열을 꺼낸다.

    샘플은 {"service", "list_total_count", "row"} 형식으로 정리된 파일이다. 원본 응답의
    RESULT는 들어 있지 않으며, 이 함수도 RESULT를 만들지 않는다.
    """
    if not isinstance(document, dict):
        raise Oa15994ParseError("샘플 문서가 객체가 아닙니다.")
    if document.get("service") != SERVICE_NAME:
        raise Oa15994ParseError(f"샘플의 service가 {SERVICE_NAME}이 아닙니다.")
    rows = document.get("row")
    total = document.get("list_total_count")
    if not isinstance(rows, list) or not isinstance(total, int) or isinstance(total, bool):
        raise Oa15994ParseError("샘플에 row 배열 또는 list_total_count가 없습니다.")
    if total != len(rows):
        raise Oa15994ParseError(f"list_total_count({total})와 row 수({len(rows)})가 다릅니다.")
    return rows
