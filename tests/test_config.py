import os
from pathlib import Path

import pytest

from anti_hacker.config import Config, load_config
from anti_hacker.errors import ConfigError


VALID_TOML = """
[[members]]
name = "m1"
model = "provider/m1:free"
role = "security-paranoid"
timeout = 90

[[members]]
name = "m2"
model = "provider/m2:free"
role = "pragmatic-engineer"
timeout = 60

[[members]]
name = "m3"
model = "provider/m3:free"
role = "adversarial-critic"
timeout = 60

[[members]]
name = "m4"
model = "provider/m4:free"
role = "code-quality"
timeout = 60

[[members]]
name = "m5"
model = "provider/m5:free"
role = "refactorer"
timeout = 60

[cartographer]
model = "provider/fast:free"
timeout = 120

[limits]
max_files_scan = 50
max_additional_file_requests = 3
debate_timeout = 180
per_member_timeout_fallback = 90
cache_ttl_seconds = 600
max_file_size_bytes = 51200

[openrouter]
base_url = "https://openrouter.ai/api/v1"
"""


def test_load_valid_config(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "council.toml").write_text(VALID_TOML, encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")

    cfg = load_config(tmp_path / "council.toml")

    assert isinstance(cfg, Config)
    assert cfg.api_key == "sk-test-123"
    assert len(cfg.members) == 5
    assert cfg.members[0].name == "m1"
    assert cfg.limits.max_files_scan == 50
    assert cfg.openrouter.base_url == "https://openrouter.ai/api/v1"


def test_missing_api_key_raises(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "council.toml").write_text(VALID_TOML, encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # Prevent load_dotenv from re-populating the env var from a real .env on disk
    monkeypatch.setattr("anti_hacker.config.load_dotenv", lambda: None)

    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        load_config(tmp_path / "council.toml")


def test_wrong_member_count_raises(tmp_path: Path, monkeypatch) -> None:
    # only 4 members
    trimmed = VALID_TOML.split("[[members]]")
    short_toml = "[[members]]".join(trimmed[:5])  # header + 4 members
    short_toml += "[cartographer]\nmodel = \"x\"\ntimeout = 60\n"
    short_toml += "[limits]\nmax_files_scan=50\nmax_additional_file_requests=3\ndebate_timeout=180\nper_member_timeout_fallback=90\ncache_ttl_seconds=600\nmax_file_size_bytes=51200\n"
    short_toml += "[openrouter]\nbase_url=\"https://openrouter.ai/api/v1\"\n"
    (tmp_path / "council.toml").write_text(short_toml, encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    with pytest.raises(ConfigError, match="exactly 5 members"):
        load_config(tmp_path / "council.toml")


def test_duplicate_member_names_raises(tmp_path: Path, monkeypatch) -> None:
    dup = VALID_TOML.replace('name = "m2"', 'name = "m1"')
    (tmp_path / "council.toml").write_text(dup, encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    with pytest.raises(ConfigError, match="unique"):
        load_config(tmp_path / "council.toml")


def test_missing_file_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nonexistent.toml")
