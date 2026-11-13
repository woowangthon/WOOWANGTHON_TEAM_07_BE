"""시각 저장·응답 변환 유틸.

규칙
- 저장: ISO 8601 UTC 문자열 (예: 2026-11-13T05:00:00Z). 초 단위까지만 쓴다.
- 응답: KST ISO 8601 문자열 (예: 2026-11-13T14:00:00+09:00).
- 타임존 정보가 없는(naive) datetime은 허용하지 않는다.
"""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
KST = ZoneInfo("Asia/Seoul")

_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_UTC_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|\+00:00)")


def _require_aware(dt: datetime) -> None:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("타임존 정보가 없는 datetime은 사용할 수 없습니다.")


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_utc_iso(dt: datetime) -> str:
    """aware datetime을 UTC 저장용 문자열로 바꾼다. 마이크로초는 버린다."""
    _require_aware(dt)
    return dt.astimezone(UTC).strftime(_UTC_FORMAT)


def parse_utc_iso(value: str) -> datetime:
    """UTC 저장용 문자열을 aware datetime(UTC)으로 바꾼다.

    'Z' 또는 '+00:00'만 허용한다. 다른 오프셋이나 타임존 없는 문자열은 거부한다.
    """
    if not isinstance(value, str) or not _UTC_PATTERN.fullmatch(value):
        raise ValueError(f"UTC 형식이 아닙니다: {value!r}")
    body = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(body)
    except ValueError as exc:
        raise ValueError(f"잘못된 시각 형식입니다: {value!r}") from exc
    return parsed.astimezone(UTC)


def to_kst_iso(dt: datetime) -> str:
    """aware datetime을 KST(+09:00) ISO 8601 응답 문자열로 바꾼다."""
    _require_aware(dt)
    return dt.astimezone(KST).isoformat(timespec="seconds")


def utc_str_to_kst_iso(value: str) -> str:
    """UTC 저장 문자열을 KST 응답 문자열로 바꾼다."""
    return to_kst_iso(parse_utc_iso(value))
