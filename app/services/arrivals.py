"""실시간 도착 정보 조회 (#40, #41). docs/api-contract.md 8.3절을 따른다.

데이터 출처(OA-12764)의 호출 주소, 서비스 이름, 응답 필드는 저장소에서 검증되지 않았다.
그래서 이 모듈은 외부 응답 형식을 가정하지 않는다. 우리가 정한 정규화 모델(ArrivalRecord)과
공급자 인터페이스(ArrivalProvider)만 정의하고, 실제 클라이언트는 검증 후 공급자로 연결한다.
공급자가 없으면 모든 조회는 UNCONFIRMED다.

규칙
- 성공한 조회만 캐시한다(기본 30초, 메모리). 실패와 미확인은 캐시하지 않는다.
- 공급자가 실패하면 이전 캐시 값을 돌려주지 않는다. 항상 UNCONFIRMED다.
- 만료된 항목은 조회 때와 저장 때 제거한다. 항목 수는 max_entries를 넘지 않는다.
- 도착 시각은 공급자가 준 남은 시간(eta_seconds)에 가져온 시각을 더해 계산한다. 값을 만들어 내지 않는다.
"""

import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.services.status_messages import (
    ARR_EMPTY,
    ARR_NOT_CONFIGURED,
    ARR_PROVIDER_FAILED,
    ARR_SNAPSHOT,
)

logger = logging.getLogger(__name__)

STATUS_CONFIRMED = "CONFIRMED"
STATUS_EMPTY = "EMPTY"
STATUS_UNCONFIRMED = "UNCONFIRMED"

REASON_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
REASON_PROVIDER_ERROR = "PROVIDER_ERROR"

DEFAULT_MAX_ENTRIES = 512
MAX_ETA_SECONDS = 24 * 3600


@dataclass(frozen=True)
class ArrivalRecord:
    """공급자가 돌려주는 정규화된 도착 한 건. 외부 응답 필드와의 대응은 공급자 구현이 맡는다(미검증)."""

    line: str | None
    direction: str | None
    destination: str | None
    eta_seconds: int  # 가져온 시점부터 도착까지 남은 시간(초)


class ArrivalProviderError(Exception):
    """공급자의 실패(네트워크, 시간 초과, 외부 오류 응답, 해석 불가). 메시지는 응답에 쓰지 않는다."""


class ArrivalProvider(Protocol):
    def fetch(self, station_id: int, station_name: str) -> list[ArrivalRecord]: ...


@dataclass(frozen=True)
class ArrivalLookup:
    status: str  # CONFIRMED / EMPTY / UNCONFIRMED
    unconfirmed_reason: str | None
    arrivals: tuple[ArrivalRecord, ...]
    fetched_at: datetime | None
    cache_hit: bool
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class _Entry:
    fetched_at: datetime
    arrivals: tuple[ArrivalRecord, ...]


class TtlCache:
    """항목 수와 보관 시간이 모두 제한된 메모리 캐시. 시계는 호출자가 주입한다."""

    def __init__(self, ttl_seconds: int, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        if ttl_seconds < 1 or max_entries < 1:
            raise ValueError("ttl_seconds와 max_entries는 1 이상이어야 합니다.")
        self._ttl = timedelta(seconds=ttl_seconds)
        self._max = max_entries
        self._items: OrderedDict[int, _Entry] = OrderedDict()

    def __len__(self) -> int:
        return len(self._items)

    def _expired(self, entry: _Entry, now: datetime) -> bool:
        age = now - entry.fetched_at
        return age > self._ttl or age < timedelta(0)  # 시계가 되돌아간 경우도 만료로 본다

    def purge(self, now: datetime) -> None:
        for key in [k for k, e in self._items.items() if self._expired(e, now)]:
            del self._items[key]

    def get(self, key: int, now: datetime) -> _Entry | None:
        entry = self._items.get(key)
        if entry is None:
            return None
        if self._expired(entry, now):
            del self._items[key]
            return None
        return entry

    def put(self, key: int, entry: _Entry, now: datetime) -> None:
        self.purge(now)
        self._items.pop(key, None)
        while len(self._items) >= self._max:
            self._items.popitem(last=False)  # 가장 오래 전에 넣은 항목부터 버린다
        self._items[key] = entry

    def discard(self, key: int) -> None:
        self._items.pop(key, None)


def _validated(records: object) -> tuple[ArrivalRecord, ...]:
    if not isinstance(records, list):
        raise ArrivalProviderError("공급자 결과가 목록이 아닙니다.")
    for r in records:
        if not isinstance(r, ArrivalRecord):
            raise ArrivalProviderError("공급자 결과 항목이 ArrivalRecord가 아닙니다.")
        eta = r.eta_seconds
        if isinstance(eta, bool) or not isinstance(eta, int) or not 0 <= eta <= MAX_ETA_SECONDS:
            raise ArrivalProviderError("eta_seconds가 허용 범위를 벗어났습니다.")
    return tuple(sorted(records, key=lambda r: (r.eta_seconds, r.line or "", r.direction or "", r.destination or "")))


class ArrivalService:
    def __init__(
        self,
        provider: ArrivalProvider | None,
        *,
        ttl_seconds: int = 30,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._provider = provider
        self.cache = TtlCache(ttl_seconds, max_entries)

    @property
    def ttl_seconds(self) -> int:
        return int(self.cache._ttl.total_seconds())

    def lookup(self, station_id: int, station_name: str, now: datetime) -> ArrivalLookup:
        if self._provider is None:
            return self._unconfirmed(REASON_NOT_CONFIGURED, ARR_NOT_CONFIGURED)

        cached = self.cache.get(station_id, now)
        if cached is not None:
            return self._ok(cached.arrivals, cached.fetched_at, cache_hit=True)

        try:
            arrivals = _validated(self._provider.fetch(station_id, station_name))
        except Exception as exc:  # 공급자의 어떤 실패도 미확인으로 처리한다. 원인 메시지는 남기지 않는다.
            logger.warning("실시간 도착 조회 실패: %s", type(exc).__name__)
            self.cache.discard(station_id)
            return self._unconfirmed(REASON_PROVIDER_ERROR, ARR_PROVIDER_FAILED)

        self.cache.put(station_id, _Entry(fetched_at=now, arrivals=arrivals), now)
        return self._ok(arrivals, now, cache_hit=False)

    @staticmethod
    def _ok(arrivals: tuple[ArrivalRecord, ...], fetched_at: datetime, *, cache_hit: bool) -> ArrivalLookup:
        if arrivals:
            return ArrivalLookup(STATUS_CONFIRMED, None, arrivals, fetched_at, cache_hit, (ARR_SNAPSHOT,))
        return ArrivalLookup(STATUS_EMPTY, None, (), fetched_at, cache_hit, (ARR_EMPTY,))

    @staticmethod
    def _unconfirmed(reason: str, message: str) -> ArrivalLookup:
        return ArrivalLookup(STATUS_UNCONFIRMED, reason, (), None, False, (message,))


# 검증된 공급자가 없다. 실제 클라이언트는 OA-12764 호출 방식을 확인한 뒤 여기에 연결한다.
_default_service: ArrivalService | None = None


def get_arrival_service() -> ArrivalService:
    """FastAPI 의존성. 캐시를 요청 사이에 유지하려고 프로세스당 하나를 쓴다. 테스트는 이 의존성을 덮어쓴다."""
    global _default_service
    if _default_service is None:
        from app.config import settings

        _default_service = ArrivalService(None, ttl_seconds=settings.arrival_cache_ttl_seconds)
    return _default_service
