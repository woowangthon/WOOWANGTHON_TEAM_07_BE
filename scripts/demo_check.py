"""시연 전 점검 스크립트. 실행 중인 서버의 주요 API가 계약대로 응답하는지 확인한다.

    uv run python scripts/demo_check.py                      # 기본: http://127.0.0.1:8000
    uv run python scripts/demo_check.py --base-url http://localhost:8001
    uv run python scripts/demo_check.py --base-url https://example.com --allow-external

판정
- PASS: 기대한 응답. WARN: 정상이지만 데이터가 없거나 미확인(실패 아님). SKIP: 앞 단계 데이터가 없어 건너뜀.
- FAIL: 연결 실패, 상태 코드·필드 불일치. 하나라도 있으면 종료 코드 1.
- 읽기 전용이다. 서버가 쓰는 DB는 바꾸지 않는다(출발 시각 API는 계산만 하고 저장하지 않는다).
- 기본은 로컬 주소(127.0.0.1, localhost, ::1)만 허용한다. 그 밖의 주소는 --allow-external을 줘야 한다.
- .env나 환경 변수를 읽거나 출력하지 않는다. 출력에는 기준 URL과 점검 결과만 나온다.
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
KST = timezone(timedelta(hours=9))
PASS, WARN, SKIP, FAIL = "PASS", "WARN", "SKIP", "FAIL"
ARRIVAL_STATUSES = {"CONFIRMED", "EMPTY", "UNCONFIRMED"}


class Report:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.results.append((status, name, detail))
        print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))

    @property
    def failed(self) -> bool:
        return any(s == FAIL for s, _, _ in self.results)

    def summary(self) -> str:
        counts = {k: sum(1 for s, _, _ in self.results if s == k) for k in (PASS, WARN, SKIP, FAIL)}
        return " ".join(f"{k}={v}" for k, v in counts.items())


def _request(client: httpx.Client, method: str, path: str, **kwargs):
    try:
        return client.request(method, path, **kwargs), None
    except httpx.HTTPError as exc:
        return None, type(exc).__name__


def _json(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        return None


def _check(report: Report, client: httpx.Client, name: str, method: str, path: str, *,
           keys: set[str], expect: int = 200, **kwargs):
    """요청 한 번과 상태 코드·필수 키 확인. 성공하면 본문을 돌려준다."""
    response, error = _request(client, method, path, **kwargs)
    if response is None:
        report.add(FAIL, name, f"요청 실패({error})")
        return None
    body = _json(response)
    if response.status_code != expect:
        report.add(FAIL, name, f"HTTP {response.status_code} (기대 {expect})")
        return None
    if not isinstance(body, dict) or not keys <= set(body):
        report.add(FAIL, name, "응답 필드가 계약과 다름")
        return None
    return body


def run_checks(client: httpx.Client, query: str = "서울역") -> Report:
    report = Report()

    body = _check(report, client, "GET /health", "GET", "/health", keys={"status"})
    if body is not None:
        report.add(PASS if body["status"] == "ok" else FAIL, "GET /health", f"status={body['status']}")
    if report.failed:
        report.add(SKIP, "나머지 점검", "서버가 응답하지 않아 중단")
        return report

    station_id = None
    body = _check(report, client, "GET /api/stations?q=", "GET", "/api/stations",
                  keys={"stations", "count", "limitations"}, params={"q": query})
    if body is not None:
        if body["count"] == 0:
            report.add(WARN, "GET /api/stations?q=", f"'{query}' 검색 결과 없음(데이터 없음은 정상)")
        else:
            station_id = body["stations"][0]["station_id"]
            report.add(PASS, "GET /api/stations?q=", f"{body['count']}건")

    if station_id is None:
        for name in ("역 상세", "역별 엘리베이터", "대체역", "도착 정보"):
            report.add(SKIP, name, "점검할 역이 없음")
    else:
        body = _check(report, client, "GET /api/stations/{id}", "GET", f"/api/stations/{station_id}",
                      keys={"station_id", "station_name"})
        if body is not None:
            report.add(PASS, "GET /api/stations/{id}", body["station_name"])

        body = _check(report, client, "GET /api/stations/{id}/elevators", "GET",
                      f"/api/stations/{station_id}/elevators",
                      keys={"station", "station_status", "elevators", "limitations"})
        if body is not None:
            status = body["station_status"]["status"]
            if status is None:
                report.add(WARN, "GET /api/stations/{id}/elevators", "역 상태 미확인(null)은 정상")
            else:
                report.add(PASS, "GET /api/stations/{id}/elevators", f"status={status}")

        body = _check(report, client, "GET /api/stations/{id}/alternatives", "GET",
                      f"/api/stations/{station_id}/alternatives",
                      keys={"alternatives", "count", "empty_reason", "limitations"})
        if body is not None:
            if body["count"] == 0:
                report.add(WARN, "GET /api/stations/{id}/alternatives", "후보 없음은 정상")
            else:
                report.add(PASS, "GET /api/stations/{id}/alternatives", f"{body['count']}건")

        body = _check(report, client, "GET /api/stations/{id}/arrivals", "GET",
                      f"/api/stations/{station_id}/arrivals",
                      keys={"arrival_status", "arrivals", "limitations"})
        if body is not None:
            status = body["arrival_status"]
            if status not in ARRIVAL_STATUSES:
                report.add(FAIL, "GET /api/stations/{id}/arrivals", f"알 수 없는 상태 {status}")
            elif status == "CONFIRMED":
                report.add(PASS, "GET /api/stations/{id}/arrivals", "CONFIRMED")
            else:
                report.add(WARN, "GET /api/stations/{id}/arrivals", f"{status}는 정상(외부 출처 미연동 포함)")

    arrival_at = (datetime.now(KST) + timedelta(days=1)).replace(microsecond=0).isoformat()
    body = _check(report, client, "POST /api/departure-time", "POST", "/api/departure-time",
                  keys={"recommended_departure_at", "estimated", "timetable_used", "elevator_warning", "limitations"},
                  json={"arrival_at": arrival_at, "travel_minutes": 30})
    if body is not None:
        ok = body["estimated"] is True and body["timetable_used"] is False
        report.add(PASS if ok else FAIL, "POST /api/departure-time",
                   "estimated=true, timetable_used=false" if ok else "추정 표시 필드가 계약과 다름")

    response, error = _request(client, "GET", "/api/stations/999999999")
    err = _json(response) if response is not None else None
    ok = response is not None and response.status_code == 404 and isinstance(err, dict) \
        and err.get("error", {}).get("code") == "NOT_FOUND"
    report.add(PASS if ok else FAIL, "오류 형식(404)", "" if ok else (error or f"HTTP {response.status_code}"))
    return report


def _is_local(base_url: str) -> bool:
    return (urlsplit(base_url).hostname or "") in LOCAL_HOSTS


def main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    p = argparse.ArgumentParser(description="시연 전 API 점검(읽기 전용).")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--allow-external", action="store_true", help="로컬이 아닌 주소 점검을 명시적으로 허용")
    p.add_argument("--query", default="서울역", help="역 검색어")
    args = p.parse_args(argv)

    parts = urlsplit(args.base_url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        print("오류: --base-url은 http(s)://host[:port] 형식이어야 합니다.", file=sys.stderr)
        return 2
    if client is None and not _is_local(args.base_url) and not args.allow_external:
        print("오류: 로컬이 아닌 주소입니다. 외부 서버를 점검하려면 --allow-external을 명시하세요.", file=sys.stderr)
        return 2

    own = client is None
    if own:
        client = httpx.Client(base_url=args.base_url.rstrip("/"), timeout=5.0, follow_redirects=False)
    print(f"점검 대상: {args.base_url}")
    try:
        report = run_checks(client, args.query)
    finally:
        if own:
            client.close()
    print(f"결과: {report.summary()}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
