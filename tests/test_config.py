from pathlib import Path

import pytest

from app.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def test_seoul_open_api_key_defaults_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEOUL_OPEN_API_KEY", raising=False)

    settings = Settings(_env_file=None)

    assert settings.seoul_open_api_key == ""


def test_env_example_allows_blank_seoul_key() -> None:
    settings = Settings(_env_file=ROOT / ".env.example")

    assert settings.seoul_open_api_key == ""


# ---------- 엘리베이터 상태 TTL 설정 (ELEVATOR_STATUS_TTL_MINUTES) ----------


def test_ttl_default_is_thirty_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVATOR_STATUS_TTL_MINUTES", raising=False)

    assert Settings(_env_file=None).elevator_status_ttl_minutes == 30


def test_ttl_can_be_overridden_by_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVATOR_STATUS_TTL_MINUTES", "60")

    assert Settings(_env_file=None).elevator_status_ttl_minutes == 60


@pytest.mark.parametrize("value", ["1", "1440"])
def test_ttl_accepts_the_inclusive_bounds(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ELEVATOR_STATUS_TTL_MINUTES", value)

    assert Settings(_env_file=None).elevator_status_ttl_minutes == int(value)


@pytest.mark.parametrize("value", ["0", "-1", "1441", "abc", "30.5", "1e2", ""])
def test_ttl_outside_integer_range_is_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ELEVATOR_STATUS_TTL_MINUTES", value)

    with pytest.raises(Exception):
        Settings(_env_file=None)


def test_ttl_bounds_match_the_judgment_module() -> None:
    from app.services.status_judgment import TTL_MINUTES_MAX as judge_max
    from app.services.status_judgment import TTL_MINUTES_MIN as judge_min
    from app.ttl_limits import TTL_MINUTES_DEFAULT, TTL_MINUTES_MAX, TTL_MINUTES_MIN

    assert (judge_min, judge_max) == (TTL_MINUTES_MIN, TTL_MINUTES_MAX) == (1, 1440)
    assert TTL_MINUTES_DEFAULT == 30
