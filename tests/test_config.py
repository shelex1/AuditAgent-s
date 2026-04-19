import os
from pathlib import Path

import pytest

from anti_hacker.config import Config, load_config
from anti_hacker.errors import ConfigError


# VALID_TOML: minimal valid config WITHOUT a legacy [openrouter] block.
# Use this for all new-format tests (with [[providers]] prepended).
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
"""

# LEGACY_VALID_TOML: same as VALID_TOML but with the old [openrouter] block appended.
# Used only for back-compat tests and legacy-format tests.
LEGACY_VALID_TOML = VALID_TOML + '\n[openrouter]\nbase_url = "https://openrouter.ai/api/v1"\n'

# Two-provider block used in new-format tests
PROVIDERS_BLOCK = """
[[providers]]
name = "openrouter"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"

[[providers]]
name = "modal"
base_url = "https://api.us-west-2.modal.direct/v1"
api_key_env = "MODAL_API_KEY"
"""


def _write(tmp_path, body):
    p = tmp_path / "council.toml"
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Pre-existing tests (updated to new Config shape)
# ---------------------------------------------------------------------------

def test_load_valid_config(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "council.toml").write_text(LEGACY_VALID_TOML, encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")

    cfg = load_config(tmp_path / "council.toml")

    assert isinstance(cfg, Config)
    # New shape: api_key lives on providers[0]
    assert cfg.providers[0].api_key == "sk-test-123"
    assert len(cfg.members) == 5
    assert cfg.members[0].name == "m1"
    assert cfg.limits.max_files_scan == 50
    # New shape: base_url lives on providers[0]
    assert cfg.providers[0].base_url == "https://openrouter.ai/api/v1"


def test_missing_api_key_raises(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "council.toml").write_text(LEGACY_VALID_TOML, encoding="utf-8")
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
    dup = LEGACY_VALID_TOML.replace('name = "m2"', 'name = "m1"')
    (tmp_path / "council.toml").write_text(dup, encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    with pytest.raises(ConfigError, match="unique"):
        load_config(tmp_path / "council.toml")


def test_missing_file_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nonexistent.toml")


# ---------------------------------------------------------------------------
# New tests: provider registry
# ---------------------------------------------------------------------------

def test_providers_registry_parsed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("MODAL_API_KEY", "modal-key")
    body = PROVIDERS_BLOCK + VALID_TOML  # VALID_TOML already defined in this file
    cfg = load_config(_write(tmp_path, body))
    names = [p.name for p in cfg.providers]
    assert names == ["openrouter", "modal"]
    assert cfg.providers[1].api_key == "modal-key"


def test_member_provider_defaults_to_first(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("MODAL_API_KEY", "modal-key")
    body = PROVIDERS_BLOCK + VALID_TOML
    cfg = load_config(_write(tmp_path, body))
    assert all(m.provider == "openrouter" for m in cfg.members)


def test_fallback_fields_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("MODAL_API_KEY", "modal-key")
    members_with_fallback = VALID_TOML.replace(
        '[[members]]\nname = "m1"\nmodel = "provider/m1:free"\nrole = "security-paranoid"\ntimeout = 90',
        '[[members]]\nname = "m1"\nmodel = "provider/m1:free"\nrole = "security-paranoid"\ntimeout = 90\n'
        'fallback_provider = "modal"\nfallback_model = "zai-org/GLM-5.1-FP8"',
    )
    cfg = load_config(_write(tmp_path, PROVIDERS_BLOCK + members_with_fallback))
    m1 = next(m for m in cfg.members if m.name == "m1")
    assert len(m1.fallbacks) == 1
    assert m1.fallbacks[0].provider == "modal"
    assert m1.fallbacks[0].model == "zai-org/GLM-5.1-FP8"


def test_fallback_provider_must_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("MODAL_API_KEY", "modal-key")
    bad = VALID_TOML.replace(
        'timeout = 90',
        'timeout = 90\nfallback_provider = "ghost"\nfallback_model = "x/y"',
        1,
    )
    with pytest.raises(ConfigError, match="unknown provider"):
        load_config(_write(tmp_path, PROVIDERS_BLOCK + bad))


def test_fallback_model_required_with_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("MODAL_API_KEY", "modal-key")
    bad = VALID_TOML.replace(
        'timeout = 90',
        'timeout = 90\nfallback_provider = "modal"',
        1,
    )
    with pytest.raises(ConfigError, match="fallback_model"):
        load_config(_write(tmp_path, PROVIDERS_BLOCK + bad))


def test_fallback_provider_cannot_equal_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("MODAL_API_KEY", "modal-key")
    bad = VALID_TOML.replace(
        'timeout = 90',
        'timeout = 90\nfallback_provider = "openrouter"\nfallback_model = "a/b"',
        1,
    )
    with pytest.raises(ConfigError, match="same provider"):
        load_config(_write(tmp_path, PROVIDERS_BLOCK + bad))


def test_missing_provider_api_key_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.delenv("MODAL_API_KEY", raising=False)
    # Prevent load_dotenv from re-populating env vars from the real .env on disk
    monkeypatch.setattr("anti_hacker.config.load_dotenv", lambda: None)
    body = PROVIDERS_BLOCK + VALID_TOML
    with pytest.raises(ConfigError, match="MODAL_API_KEY"):
        load_config(_write(tmp_path, body))


# ---------------------------------------------------------------------------
# Helpers for new fallback/provider-flag tests
# ---------------------------------------------------------------------------

_MINIMAL_VALID_CONFIG = """
[[providers]]
name = "openrouter"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"
""" + VALID_TOML

_MINIMAL_VALID_CONFIG_WITH_FLAG = """
[[providers]]
name = "openrouter"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"
empty_means_quota = true
""" + VALID_TOML


def test_provider_without_api_key_env_loads_with_empty_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    toml = tmp_path / "council.toml"
    toml.write_text(_MINIMAL_VALID_CONFIG + """
[[providers]]
name = "ollama"
base_url = "http://localhost:11434/v1"
""")
    cfg = load_config(toml)
    ollama = next(p for p in cfg.providers if p.name == "ollama")
    assert ollama.api_key == ""
    assert ollama.api_key_env is None


_THREE_PROVIDERS_BLOCK = """
[[providers]]
name = "openrouter"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"

[[providers]]
name = "modal"
base_url = "https://api.us-west-2.modal.direct/v1"
api_key_env = "MODAL_API_KEY"

[[providers]]
name = "ollama"
base_url = "http://localhost:11434/v1"
"""

_CONFIG_WITH_FALLBACKS_LIST = _THREE_PROVIDERS_BLOCK + """
[[members]]
name = "trinity-large"
model = "arcee-ai/trinity-large-preview:free"
role = "security-paranoid"
timeout = 120
provider = "openrouter"
fallbacks = [
  { provider = "modal",  model = "zai-org/GLM-5-FP8-2" },
  { provider = "ollama", model = "minimax-m2.7:cloud" },
]

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
"""

_CONFIG_WITH_LEGACY_FALLBACK = PROVIDERS_BLOCK + """
[[members]]
name = "m1"
model = "provider/m1:free"
role = "security-paranoid"
timeout = 90
fallback_provider = "modal"
fallback_model = "zai-org/GLM-5-FP8-2"

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
"""

_CONFIG_WITH_BOTH_LEGACY_AND_LIST = PROVIDERS_BLOCK + """
[[members]]
name = "m1"
model = "provider/m1:free"
role = "security-paranoid"
timeout = 90
fallback_provider = "modal"
fallback_model = "zai-org/GLM-5-FP8-2"
fallbacks = [
  { provider = "modal", model = "zai-org/GLM-5-FP8-2" },
]

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
"""

_CONFIG_WITH_FALLBACK_TO_UNKNOWN_PROVIDER = PROVIDERS_BLOCK + """
[[members]]
name = "m1"
model = "provider/m1:free"
role = "security-paranoid"
timeout = 90
fallbacks = [
  { provider = "ghost", model = "x/y" },
]

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
"""


def test_member_fallbacks_list_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("MODAL_API_KEY", "mk")
    toml = tmp_path / "council.toml"
    toml.write_text(_CONFIG_WITH_FALLBACKS_LIST)
    cfg = load_config(toml)
    m = next(m for m in cfg.members if m.name == "trinity-large")
    assert len(m.fallbacks) == 2
    assert m.fallbacks[0].provider == "modal"
    assert m.fallbacks[1].provider == "ollama"


def test_legacy_fallback_fields_synthesize_one_element_list(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("MODAL_API_KEY", "mk")
    toml = tmp_path / "council.toml"
    toml.write_text(_CONFIG_WITH_LEGACY_FALLBACK)
    cfg = load_config(toml)
    m = cfg.members[0]
    assert len(m.fallbacks) == 1
    assert m.fallbacks[0].provider == "modal"
    assert m.fallbacks[0].model == "zai-org/GLM-5-FP8-2"


def test_mixing_legacy_and_fallbacks_list_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("MODAL_API_KEY", "mk")
    toml = tmp_path / "council.toml"
    toml.write_text(_CONFIG_WITH_BOTH_LEGACY_AND_LIST)
    with pytest.raises(ConfigError, match="cannot mix"):
        load_config(toml)


def test_fallback_entry_unknown_provider_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("MODAL_API_KEY", "mk")
    toml = tmp_path / "council.toml"
    toml.write_text(_CONFIG_WITH_FALLBACK_TO_UNKNOWN_PROVIDER)
    with pytest.raises(ConfigError, match="unknown provider"):
        load_config(toml)


def test_provider_empty_means_quota_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    toml = tmp_path / "council.toml"
    toml.write_text(_MINIMAL_VALID_CONFIG_WITH_FLAG)
    cfg = load_config(toml)
    openr = next(p for p in cfg.providers if p.name == "openrouter")
    assert openr.empty_means_quota is True


def test_back_compat_openrouter_block(tmp_path, monkeypatch):
    # Old format: no [[providers]], only [openrouter]
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    old = VALID_TOML + '\n[openrouter]\nbase_url = "https://openrouter.ai/api/v1"\n'
    cfg = load_config(_write(tmp_path, old))
    assert len(cfg.providers) == 1
    assert cfg.providers[0].name == "openrouter"
    assert cfg.providers[0].api_key == "or-key"
    assert cfg.providers[0].base_url == "https://openrouter.ai/api/v1"
