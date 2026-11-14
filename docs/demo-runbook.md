# 시연 운영 런북 (#44, #45)

시연 서버(집 데스크탑, 터미널)를 준비하고 점검하는 절차와 실행 기록이다. 명령 전체와 옵션은 [README.md](../README.md)의 "시연 서버 실행"과 "공공데이터 갱신 실행 방식"이 원본이고, 이 문서는 **실행 기록, 실패 시 동작, 점검 호출 예, 백업**을 모은다. 확인하지 못한 것은 `(미검증)`으로 표시한다.

## 1. 갱신 실행 방식 (#44)

**MVP는 수동 CLI**로 정했다(2026-11-14). 시연 직전에 터미널에서 `collect_elevators --apply`를 다시 실행한다. 비교와 근거는 README "공공데이터 갱신 실행 방식 (#44)" 표를 본다.

| 후보 | 결과 | 이유 |
|---|---|---|
| 수동 CLI | 선택 | 시연 PC에서 직접 실행, 결과를 눈으로 확인 |
| 로컬 스케줄러(Windows 작업 스케줄러 등) | 보류 | 시연 직전 1회 수동 실행으로 충분. 필요하면 같은 명령을 등록한다 |
| GitHub Actions `workflow_dispatch` | 보조 | 컨테이너가 임시라 운영 DB에 반영되지 않는다. dry-run 확인용 |
| GitHub Actions `schedule` | 보류 | 호출 한도 미확인, 영속 저장소 없음 |

시간표(`collect_timetable`)와 인접 간선(`derive_edges`)은 자주 바뀌지 않으므로 한 번만 준비한다. 상태가 신선해야 하는 것은 엘리베이터뿐이다.

## 2. 실행 기록: 2026-11-14 (시연 PC, `data/dev.db`)

| 단계 | 결과 |
|---|---|
| 마이그레이션·시드 | 역 240, 노선 8, 환승 간선 38 |
| `collect_elevators --apply` | 설비 866건 생성, 미매핑 0건 |
| `collect_timetable --station-code 0150 --station-code 0222 --day-type WEEKDAY --apply` (시험) | 967행, 오류·한도 문제 없음 |
| `collect_timetable --apply` 전체 1차 | **적재 오류로 전체 롤백**: `같은 열차 번호가 중복되었습니다: 0211 HOLIDAY 1 2022`. 시간표는 시험분 967행 그대로 |
| `collect_timetable --apply` 전체 2차(순환 종착 병합 수정 후) | 279,458행 적재, 병합 1,142건, 오류·한도 문제 없음 |
| `derive_edges --apply` | 인접 간선 274개 추출. 5호선 오류 간선 5건을 `--prune`으로 지워 **269개** |

- 위 수치는 이 날 실행한 서브에이전트의 보고를 옮긴 것이다. 이 문서를 쓰면서 다시 실행하지 않았다.
- 실행 전에 `data/dev.db` 백업(`dev.db.bak`)을 만들었는지 이 기록에는 남아 있지 않다. 다음 적재부터 아래 5절 절차로 반드시 남긴다 (미검증).
- 호출 한도 오류는 없었지만 **일일 한도 자체는 확인하지 못했다.**

## 3. 실패 시 동작 (코드로 확인한 사실)

완료 기준 "실패 시 `fetched_at`이 갱신되지 않는다"에 대해 코드를 읽어 확인한 내용이다.

- 엘리베이터 적재는 전체가 한 트랜잭션이고 실패하면 롤백한다([elevator_ingest.py](../app/services/elevator_ingest.py)의 설명과 `ingest_elevators`의 `rollback`). 롤백되면 `fetched_at`도 바뀌지 않는다.
- 주입한 `fetched_at`이 저장된 값보다 과거이면 전체를 롤백하고 오류로 끝낸다(오래된 실행이 최신 값을 덮지 못한다).
- 외부 API 오류(전송·응답 오류)는 종료 코드 3이고 "기존 DB와 `fetched_at`은 그대로다"([collect_elevators.py](../app/cli/collect_elevators.py) 머리 설명).
- 시간표 적재도 실패하면 롤백하고 예외를 던진다([timetable_ingest.py](../app/services/timetable_ingest.py)). 위 2절의 1차 실행에서 실제로 롤백되어 시험분 967행이 그대로 남았다.
- `tests/test_elevator_ingest.py`에 롤백·과거 `fetched_at` 관련 테스트가 있다. 이 문서 작성 중에는 실행하지 않았다.
- 실제 시연 PC에서 의도적으로 API 실패를 일으켜 `fetched_at`이 유지되는지 확인하지는 않았다 (미검증).
- 수집 후 `data/logs/ingestion_issues.jsonl`에 `run_id`별 결과가 쌓인다(README).

## 4. 시연 데이터 준비 순서 (#45)

README "1회 준비"의 순서를 따른다. 요약:

1. `uv sync`, `.env` 작성(키와 `CORS_ALLOWED_ORIGINS`), `uv run alembic upgrade head`
2. **서버를 멈춘 상태에서 `data/dev.db` 백업**
3. `load_seed` → `load_edges --apply` → `collect_elevators --apply` → `collect_timetable --apply` → `derive_edges --apply`
4. 모든 단계는 `--apply` 없이 먼저 dry-run으로 건수를 확인한다.
5. `collect_timetable` 전체는 약 1,700회 호출한다. 처음에는 `--station-code ... --day-type WEEKDAY`로 일부만 시험한다.
6. 간선을 다시 추출한 뒤 오류 간선을 지우려면 `derive_edges --prune`을 쓰되, **시간표를 전체 수집한 DB에서만** 쓴다(부분 수집 상태에서는 유효한 간선도 지울 수 있다).

## 5. 영속·백업 (SQLite)

- 운영 DB는 `data/dev.db` 한 파일이고 서버 프로세스는 1개다. Docker·Redis·PostgreSQL은 쓰지 않는다.
- 백업: **서버를 멈춘 뒤** 파일을 복사한다.

```powershell
Copy-Item data/dev.db "data/dev.db.bak-$(Get-Date -Format yyyyMMdd-HHmm)"
```

- 복원: 서버를 멈추고 백업 파일을 `data/dev.db`로 복사해 덮어쓴 뒤 서버를 시작한다.
- `data/dev.db`와 백업 파일은 커밋하지 않는다(커밋 전 `git status`로 확인).
- 백업 파일 복원 절차는 이 문서 작성 중 실행해 확인하지 않았다 (미검증).

## 6. 설정: CORS·`.env`

- `.env`는 저장소에 올리지 않는다. 키(`SEOUL_OPEN_API_KEY`)는 이 PC의 `.env`에만 두고 문서·채팅·로그에 쓰지 않는다.
- FE가 다른 PC·포트에서 호출하면 `CORS_ALLOWED_ORIGINS`에 그 Origin(예: `http://192.168.0.10:5173`)을 넣고 서버를 다시 시작한다.
- 같은 네트워크에서 접근하려면 `--host 0.0.0.0`으로 실행하고 방화벽에서 포트 8000을 연다. 인증이 없으므로 외부 인터넷에는 열지 않는다.

## 7. 시연 전 점검 (호출 예)

서버: `uv run uvicorn app.main:app --host 0.0.0.0 --port 8000`. 역 id는 먼저 검색으로 찾는다.

```bash
curl http://127.0.0.1:8000/health
curl "http://127.0.0.1:8000/api/stations?q=강남"                 # 한글은 --data-urlencode나 Python httpx 권장(Git Bash curl은 한글을 깨뜨림)
curl http://127.0.0.1:8000/api/stations/{id}/elevators             # 역별 상태
curl "http://127.0.0.1:8000/api/elevators/status?status=가동%20안됨&line=2"
curl http://127.0.0.1:8000/api/stations/{id}/alternatives          # 대체역
curl "http://127.0.0.1:8000/api/routes?from={id}&to={id}"          # 경로
curl -X POST http://127.0.0.1:8000/api/departure-time \
  -H "Content-Type: application/json" \
  -d '{"arrival_at":"2026-12-01T09:00:00+09:00","travel_minutes":30}'
```

- 출발 시각에 `from_station_id`·`to_station_id`를 더하면 경로 위 엘리베이터 경고를 함께 받는다(`docs/frontend-integration.md`).
- 자동 점검: `uv run python scripts/demo_check.py`가 `FAIL=0`이면 통과한다. WARN(정보 없음 등)은 정상이다.
- 엘리베이터 상태는 수집 시각이 TTL(120분, 임시값)을 넘으면 STALE로 나온다. **시연 직전에 `collect_elevators --apply`를 다시 실행한다.**
- FE 화면에서 브라우저 콘솔에 CORS 오류가 없는지 확인한다.

## 8. 팀 확인이 필요한 항목

- 데모 DB의 합성 상태 `[DEMO/SYNTHETIC]` 5건이 Mock 금지 규칙에 걸리는지. 이 문서는 결정하지 않는다. 시연 DB(`data/dev.db`)에 현재 남아 있는지도 확인하지 않았다 (미검증).
- 일일 호출 한도(OA-15994, OA-101).
- 시연에서 경로·출발 시각을 어떻게 표현할지(추정값, 시간표에서 추출한 간선).
