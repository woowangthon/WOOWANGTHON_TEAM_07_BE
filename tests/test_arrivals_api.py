"""GET /api/stations/{id}/arrivals 와 도착 정보 캐시 검증. 외부 API는 호출하지 않고 가짜 공급자만 쓴다."""

from datetime import datetime, timedelta, timezone

import pytest

from app.main import create_app
from app.database import get_session
from app.services import arrivals as arrivals_module
from app.services.arrivals import (
    ArrivalProviderError,
    ArrivalRecord,
    ArrivalService,
    TtlCache,
    get_arrival_service,
)
from app.services.status_messages import ARR_EMPTY, ARR_NOT_CONFIGURED, ARR_PROVIDER_FAILED, ARR_SNAPSHOT

T0 = datetime(2026, 11, 14, 3, 0, 0, tzinfo=timezone.utc)


class FakeProvider:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def fetch(self, station_id, station_name):
        self.calls.append((station_id, station_name))
        if self.error is not None:
            raise self.error
        return self.result


def rec(eta=120, line="2", direction="내선", destination="시청"):
    return ArrivalRecord(line=line, direction=direction, destination=destination, eta_seconds=eta)


@pytest.fixture
def now_box(monkeypatch):
    box = {"now": T0}
    monkeypatch.setattr("app.routers.arrivals.utc_now", lambda: box["now"])
    return box


def make_client(db, service):
    from fastapi.testclient import TestClient

    app = create_app()
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_arrival_service] = lambda: service
    return TestClient(app)


# ---------- 공급자 없음 / 정상 / 빈 응답 / 오류 ----------


def test_no_provider_is_unconfirmed(client, now_box):
    body = client.get("/api/stations/1/arrivals").json()  # 기본 서비스는 공급자가 없다

    assert body["arrival_status"] == "UNCONFIRMED"
    assert body["unconfirmed_reason"] == "PROVIDER_NOT_CONFIGURED"
    assert body["arrivals"] == [] and body["count"] == 0
    assert body["fetched_at"] is None and body["cache_hit"] is False
    assert body["limitations"] == [ARR_NOT_CONFIGURED]


def test_default_service_has_no_provider_and_no_network():
    arrivals_module._default_service = None
    service = get_arrival_service()

    result = service.lookup(1, "서울역", T0)

    assert result.status == "UNCONFIRMED" and result.arrivals == ()
    assert service.ttl_seconds == 30
    arrivals_module._default_service = None


def test_success_returns_confirmed_with_kst_times(db, now_box):
    provider = FakeProvider([rec(300, destination="B"), rec(60, destination="A")])
    body = make_client(db, ArrivalService(provider)).get("/api/stations/1/arrivals").json()

    assert body["arrival_status"] == "CONFIRMED" and body["unconfirmed_reason"] is None
    assert [a["destination"] for a in body["arrivals"]] == ["A", "B"]  # 남은 시간 순
    assert body["fetched_at"] == "2026-11-14T12:00:00+09:00"
    assert body["arrivals"][0]["estimated_arrival_at"] == "2026-11-14T12:01:00+09:00"
    assert body["arrivals"][1]["eta_seconds"] == 300
    assert body["count"] == 2 and body["cache_hit"] is False and body["cache_ttl_seconds"] == 30
    assert body["limitations"] == [ARR_SNAPSHOT]
    assert provider.calls == [(1, "서울역")]


def test_empty_response_is_empty_not_unconfirmed(db, now_box):
    body = make_client(db, ArrivalService(FakeProvider([]))).get("/api/stations/1/arrivals").json()

    assert body["arrival_status"] == "EMPTY" and body["arrivals"] == [] and body["count"] == 0
    assert body["fetched_at"] == "2026-11-14T12:00:00+09:00"
    assert body["limitations"] == [ARR_EMPTY]


@pytest.mark.parametrize("error", [ArrivalProviderError("x"), TimeoutError("t"), RuntimeError("boom")])
def test_provider_error_is_unconfirmed(db, now_box, error):
    response = make_client(db, ArrivalService(FakeProvider(error=error))).get("/api/stations/1/arrivals")

    body = response.json()
    assert response.status_code == 200
    assert body["arrival_status"] == "UNCONFIRMED" and body["unconfirmed_reason"] == "PROVIDER_ERROR"
    assert body["arrivals"] == [] and body["fetched_at"] is None
    assert body["limitations"] == [ARR_PROVIDER_FAILED]
    assert "boom" not in response.text


@pytest.mark.parametrize("bad", [
    None, "x", [object()], [rec(-1)], [rec(10**9)], [ArrivalRecord("2", None, None, True)],
    [ArrivalRecord("2", None, None, 1.5)],
])
def test_malformed_provider_output_is_unconfirmed(db, now_box, bad):
    body = make_client(db, ArrivalService(FakeProvider(bad))).get("/api/stations/1/arrivals").json()

    assert body["arrival_status"] == "UNCONFIRMED" and body["arrivals"] == []


# ---------- 캐시 ----------


def test_cache_hit_within_ttl_calls_provider_once(db, now_box):
    provider = FakeProvider([rec()])
    client = make_client(db, ArrivalService(provider))

    first = client.get("/api/stations/1/arrivals").json()
    now_box["now"] = T0 + timedelta(seconds=30)  # 경계값은 아직 유효
    second = client.get("/api/stations/1/arrivals").json()

    assert len(provider.calls) == 1
    assert first["cache_hit"] is False and second["cache_hit"] is True
    assert second["fetched_at"] == first["fetched_at"]


def test_cache_expires_after_ttl(db, now_box):
    provider = FakeProvider([rec()])
    client = make_client(db, ArrivalService(provider))

    client.get("/api/stations/1/arrivals")
    now_box["now"] = T0 + timedelta(seconds=31)
    body = client.get("/api/stations/1/arrivals").json()

    assert len(provider.calls) == 2 and body["cache_hit"] is False
    assert body["fetched_at"] == "2026-11-14T12:00:31+09:00"


def test_failure_after_expiry_never_returns_old_value(db, now_box):
    provider = FakeProvider([rec()])
    service = ArrivalService(provider)
    client = make_client(db, service)
    client.get("/api/stations/1/arrivals")

    now_box["now"] = T0 + timedelta(seconds=31)
    provider.error = ArrivalProviderError("down")
    body = client.get("/api/stations/1/arrivals").json()

    assert body["arrival_status"] == "UNCONFIRMED" and body["arrivals"] == []
    assert len(service.cache) == 0


def test_failure_within_ttl_is_not_masked_by_cache_when_entry_missing(db, now_box):
    # 실패 결과는 캐시하지 않는다. 다음 조회는 공급자를 다시 부른다.
    provider = FakeProvider(error=ArrivalProviderError("down"))
    service = ArrivalService(provider)
    client = make_client(db, service)

    client.get("/api/stations/1/arrivals")
    provider.error, provider.result = None, [rec()]
    body = client.get("/api/stations/1/arrivals").json()

    assert len(provider.calls) == 2 and body["arrival_status"] == "CONFIRMED"


def test_empty_result_is_cached_too(db, now_box):
    provider = FakeProvider([])
    client = make_client(db, ArrivalService(provider))

    client.get("/api/stations/1/arrivals")
    body = client.get("/api/stations/1/arrivals").json()

    assert len(provider.calls) == 1 and body["cache_hit"] is True and body["arrival_status"] == "EMPTY"


def test_cache_is_per_station(db, now_box):
    provider = FakeProvider([rec()])
    client = make_client(db, ArrivalService(provider))

    client.get("/api/stations/1/arrivals")
    client.get("/api/stations/2/arrivals")

    assert [c[0] for c in provider.calls] == [1, 2]


def test_ttl_cache_purges_expired_and_is_bounded():
    cache = TtlCache(ttl_seconds=30, max_entries=3)
    entry = lambda t: arrivals_module._Entry(fetched_at=t, arrivals=())

    for i in range(10):
        cache.put(i, entry(T0), T0)
        assert len(cache) <= 3
    assert len(cache) == 3 and cache.get(9, T0) is not None and cache.get(0, T0) is None

    later = T0 + timedelta(seconds=31)
    cache.put(100, entry(later), later)  # 저장 때 만료 항목을 모두 제거한다
    assert len(cache) == 1


def test_ttl_cache_many_unique_keys_do_not_grow_unbounded():
    cache = TtlCache(ttl_seconds=30, max_entries=50)
    for i in range(5000):
        t = T0 + timedelta(seconds=i)
        cache.put(i, arrivals_module._Entry(fetched_at=t, arrivals=()), t)

    assert len(cache) <= 50


def test_clock_going_backwards_counts_as_expired():
    cache = TtlCache(ttl_seconds=30)
    cache.put(1, arrivals_module._Entry(fetched_at=T0, arrivals=()), T0)

    assert cache.get(1, T0 - timedelta(seconds=1)) is None
    assert len(cache) == 0


def test_cache_ttl_validation():
    with pytest.raises(ValueError):
        TtlCache(ttl_seconds=0)
    with pytest.raises(ValueError):
        TtlCache(ttl_seconds=30, max_entries=0)


def test_ttl_comes_from_settings(monkeypatch):
    from app import config

    monkeypatch.setattr(config.settings, "arrival_cache_ttl_seconds", 45)
    arrivals_module._default_service = None
    try:
        assert get_arrival_service().ttl_seconds == 45
    finally:
        arrivals_module._default_service = None


# ---------- 입력 오류 / 계약 ----------


@pytest.mark.parametrize("station_id", [99, 0, -1])
def test_missing_station_is_404_and_provider_not_called(db, now_box, station_id):
    provider = FakeProvider([rec()])
    response = make_client(db, ArrivalService(provider)).get(f"/api/stations/{station_id}/arrivals")

    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"
    assert provider.calls == []


def test_non_integer_station_is_422(client):
    response = client.get("/api/stations/abc/arrivals")

    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_post_is_405(client):
    assert client.post("/api/stations/1/arrivals").status_code == 405


def test_response_fields(db, now_box):
    body = make_client(db, ArrivalService(FakeProvider([rec()]))).get("/api/stations/1/arrivals").json()

    assert set(body) == {"station", "arrival_status", "unconfirmed_reason", "arrivals", "count",
                         "fetched_at", "cache_hit", "cache_ttl_seconds", "limitations"}
    assert set(body["arrivals"][0]) == {"line", "direction", "destination", "eta_seconds",
                                        "estimated_arrival_at"}
    assert set(body["station"]) == {"station_id", "station_name"}


def test_optional_arrival_fields_may_be_null(db, now_box):
    provider = FakeProvider([ArrivalRecord(None, None, None, 0)])
    body = make_client(db, ArrivalService(provider)).get("/api/stations/1/arrivals").json()

    assert body["arrivals"][0]["line"] is None and body["arrivals"][0]["estimated_arrival_at"] == body["fetched_at"]
