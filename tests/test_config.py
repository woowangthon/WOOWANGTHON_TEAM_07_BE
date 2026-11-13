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
