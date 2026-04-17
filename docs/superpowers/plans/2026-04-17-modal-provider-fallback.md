# Modal Provider Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in per-member fallback from OpenRouter to Modal Research (second OpenAI-compatible provider), triggered only when the primary exhausts retries on rate-limit / quota errors.

**Architecture:** Introduce a named provider registry in `config/council.toml`; instantiate one `OpenRouterClient` per provider; each `CouncilMember` holds a primary client+model and optional fallback client+model. On `rate_limit`-class errors the member makes one more call against the fallback. Other error classes still lead to `abstained`, unchanged.

**Tech Stack:** Python 3.11, Pydantic v2, httpx, pytest, pytest-asyncio, `uv`/`pip` per `pyproject.toml`.

**Spec:** `docs/superpowers/specs/2026-04-17-modal-provider-fallback-design.md`

**Run tests with:** `pytest -q` from the repo root.

---

## File Structure

**Modify:**
- `src/anti_hacker/errors.py` — `OpenRouterError` gains a `kind` attribute (`rate_limit | timeout | upstream | malformed | network | None`).
- `src/anti_hacker/openrouter/client.py` — set `kind` on every terminal raise; add per-call `max_retries` kwarg to `chat()`; update docstring.
- `src/anti_hacker/config.py` — new `ProviderConfig` model; `MemberConfig` gains `provider`, `fallback_provider`, `fallback_model`; new validators; loader resolves env keys per provider; back-compat shim for old `[openrouter]` block.
- `src/anti_hacker/council/member.py` — `CouncilMember` holds `primary_client`, `primary_model`, optional `fallback_client`, `fallback_model`; `ask()` returns a structured `MemberReply(text, provider, via_fallback)`; fallback dispatch on `kind == "rate_limit"`.
- `src/anti_hacker/council/orchestra.py` — consume new `MemberReply`, record `provider`/`via_fallback` per round into a new `result.member_meta`.
- `src/anti_hacker/io/debate_log.py` — accept and persist `member_meta` alongside `responses`.
- `src/anti_hacker/tools/consult.py` — build `clients: dict[str, OpenRouterClient]` from the registry, pass the pair to each `CouncilMember`, feed `member_meta` into the log.
- `src/anti_hacker/server.py` — replace single-client wiring with registry-based wiring; update `Cartographer` to use the first provider's client.
- `config/council.toml` — migrate to `[[providers]]`; add `fallback_*` entries the user wants.

**Create:**
- `tests/test_member_fallback.py` — new tests for member-level fallback logic.

**Test updates:**
- `tests/test_config.py` — add cases for providers + fallback fields + back-compat.
- `tests/test_openrouter_client.py` — cases for `kind` classification and per-call `max_retries`.
- `tests/test_orchestra.py` / `tests/test_tools_consult.py` — updates for new `MemberReply` / `member_meta` plumbing.

---

## Task 1: Add `kind` to `OpenRouterError`

**Files:**
- Modify: `src/anti_hacker/errors.py`
- Test: `tests/test_openrouter_client.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_openrouter_client.py`:

```python
def test_openrouter_error_has_kind_default_none():
    from anti_hacker.errors import OpenRouterError
    err = OpenRouterError("boom")
    assert err.kind is None

def test_openrouter_error_kind_can_be_set():
    from anti_hacker.errors import OpenRouterError
    err = OpenRouterError("rate limited", kind="rate_limit")
    assert err.kind == "rate_limit"
    assert str(err) == "rate limited"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_openrouter_client.py::test_openrouter_error_has_kind_default_none -v`
Expected: FAIL — `TypeError` on `kind=` kwarg.

- [ ] **Step 3: Implement `kind`**

Replace `OpenRouterError` in `src/anti_hacker/errors.py`:

```python
from typing import Literal, Optional

ErrorKind = Literal["rate_limit", "timeout", "upstream", "malformed", "network"]


class OpenRouterError(AntiHackerError):
    """Raised on unrecoverable OpenRouter / OpenAI-compatible API failures."""

    def __init__(self, message: str, *, kind: Optional[ErrorKind] = None) -> None:
        super().__init__(message)
        self.kind: Optional[ErrorKind] = kind
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_openrouter_client.py -v -k error_has_kind or test_openrouter_error_kind_can_be_set`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/anti_hacker/errors.py tests/test_openrouter_client.py
git commit -m "feat(errors): add kind field to OpenRouterError"
```

---

## Task 2: Classify errors in `OpenRouterClient` and add per-call `max_retries`

**Files:**
- Modify: `src/anti_hacker/openrouter/client.py`
- Test: `tests/test_openrouter_client.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_openrouter_client.py`:

```python
import httpx
import pytest
from anti_hacker.errors import OpenRouterError
from anti_hacker.openrouter.client import OpenRouterClient


def _mock_transport(responses):
    call = {"i": 0}

    async def handler(request):
        i = call["i"]
        call["i"] += 1
        status, body, headers = responses[min(i, len(responses) - 1)]
        return httpx.Response(status, json=body, headers=headers or {})

    return httpx.MockTransport(handler), call


@pytest.mark.asyncio
async def test_429_terminal_sets_kind_rate_limit():
    transport, _ = _mock_transport([(429, {}, {"retry-after": "0"})] * 3)
    client = OpenRouterClient(
        api_key="k", base_url="http://x", transport=transport,
        retry_backoff=lambda a: 0.0, max_retries=3,
    )
    with pytest.raises(OpenRouterError) as ei:
        await client.chat(model="m", system="s", user="u", timeout=1.0)
    assert ei.value.kind == "rate_limit"


@pytest.mark.asyncio
async def test_5xx_terminal_sets_kind_upstream():
    transport, _ = _mock_transport([(503, {}, None)] * 3)
    client = OpenRouterClient(
        api_key="k", base_url="http://x", transport=transport,
        retry_backoff=lambda a: 0.0, max_retries=3,
    )
    with pytest.raises(OpenRouterError) as ei:
        await client.chat(model="m", system="s", user="u", timeout=1.0)
    assert ei.value.kind == "upstream"


@pytest.mark.asyncio
async def test_timeout_sets_kind_timeout():
    async def handler(request):
        raise httpx.TimeoutException("slow")
    transport = httpx.MockTransport(handler)
    client = OpenRouterClient(
        api_key="k", base_url="http://x", transport=transport,
        retry_backoff=lambda a: 0.0, max_retries=2,
    )
    with pytest.raises(OpenRouterError) as ei:
        await client.chat(model="m", system="s", user="u", timeout=1.0)
    assert ei.value.kind == "timeout"


@pytest.mark.asyncio
async def test_per_call_max_retries_overrides_instance():
    transport, counter = _mock_transport([(429, {}, {"retry-after": "0"})] * 10)
    client = OpenRouterClient(
        api_key="k", base_url="http://x", transport=transport,
        retry_backoff=lambda a: 0.0, max_retries=5,
    )
    with pytest.raises(OpenRouterError):
        await client.chat(model="m", system="s", user="u", timeout=1.0, max_retries=1)
    assert counter["i"] == 1
```

- [ ] **Step 2: Run to confirm failures**

Run: `pytest tests/test_openrouter_client.py -v -k "kind or max_retries_overrides"`
Expected: FAIL — `kind` missing, or `chat()` does not accept `max_retries` kwarg.

- [ ] **Step 3: Patch `OpenRouterClient.chat`**

In `src/anti_hacker/openrouter/client.py`:

1. Update docstring:

```python
class OpenRouterClient:
    """Async client for any OpenAI-compatible Chat Completions endpoint.

    Named `OpenRouterClient` for historical reasons; instantiate one per
    provider (OpenRouter, Modal, etc.) with its own base_url and api_key.
    """
```

2. Change `chat()` signature to accept `max_retries`:

```python
async def chat(
    self,
    *,
    model: str,
    system: str,
    user: str,
    timeout: float,
    response_format_json: bool = True,
    max_retries: int | None = None,
) -> OpenRouterResponse:
    ...
    effective_max = self._max_retries if max_retries is None else max_retries
    ...
    while attempt < effective_max:
        ...
```

Replace every reference to `self._max_retries` inside `chat()` with `effective_max`.

3. Wrap every terminal `raise` with the right `kind`. At each site:

```python
# 429 exhausted path — at the `last_exc = OpenRouterError(...)` line for 429:
last_exc = OpenRouterError(f"rate limit (attempt {attempt + 1})", kind="rate_limit")

# 5xx path:
last_exc = OpenRouterError(f"upstream {r.status_code} (attempt {attempt + 1})", kind="upstream")

# Timeout path (httpx.TimeoutException):
last_exc = OpenRouterError(f"timeout after {timeout}s (attempt {attempt + 1})", kind="timeout")

# httpx.HTTPError path:
last_exc = OpenRouterError(f"network error: {exc}", kind="network")

# unexpected status (non-429, non-5xx) -> immediate raise:
raise OpenRouterError(f"unexpected status {r.status_code}: {r.text[:200]}", kind="upstream")

# malformed (empty choices / empty content / parse error):
raise OpenRouterError("malformed response: empty choices", kind="malformed")
raise OpenRouterError("malformed response: empty content", kind="malformed")
raise OpenRouterError(f"malformed response: {exc}", kind="malformed") from exc

# unreachable guard at end:
raise OpenRouterError("retry loop exited without an exception (unreachable)", kind="upstream")
```

- [ ] **Step 4: Run all client tests**

Run: `pytest tests/test_openrouter_client.py -v`
Expected: all PASS (new 4 tests + pre-existing tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/anti_hacker/openrouter/client.py tests/test_openrouter_client.py
git commit -m "feat(openrouter): classify error kinds and support per-call max_retries"
```

---

## Task 3: Extend `config.py` — provider registry + member fallback fields

**Files:**
- Modify: `src/anti_hacker/config.py`
- Test: `tests/test_config.py`

### Step 1: Write failing tests

- [ ] Append the following tests to `tests/test_config.py` (reusing existing fixtures for env setup):

```python
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
    assert m1.fallback_provider == "modal"
    assert m1.fallback_model == "zai-org/GLM-5.1-FP8"


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
    body = PROVIDERS_BLOCK + VALID_TOML
    with pytest.raises(ConfigError, match="MODAL_API_KEY"):
        load_config(_write(tmp_path, body))


def test_back_compat_openrouter_block(tmp_path, monkeypatch):
    # Old format: no [[providers]], only [openrouter]
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    old = VALID_TOML + '\n[openrouter]\nbase_url = "https://openrouter.ai/api/v1"\n'
    cfg = load_config(_write(tmp_path, old))
    assert len(cfg.providers) == 1
    assert cfg.providers[0].name == "openrouter"
    assert cfg.providers[0].api_key == "or-key"
    assert cfg.providers[0].base_url == "https://openrouter.ai/api/v1"
```

Note: `VALID_TOML` lacks the trailing bytes; keep `max_file_size_bytes = 51200` plus `[openrouter] base_url = "..."` already inside the fixture if the shim test needs it. If the current `VALID_TOML` constant already includes the legacy `[openrouter]` block, strip it for the new-format tests and keep it only for `test_back_compat_openrouter_block`. Inspect the file and adjust the constant split accordingly (e.g. introduce a `LEGACY_VALID_TOML` constant).

- [ ] **Step 2: Run to confirm failures**

Run: `pytest tests/test_config.py -v -k "providers or fallback or back_compat or api_key_env"`
Expected: FAIL — new models/validators not yet present.

### Step 3: Implement provider registry and validators

- [ ] Replace `src/anti_hacker/config.py` with:

```python
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

from .errors import ConfigError


Role = Literal[
    "security-paranoid",
    "pragmatic-engineer",
    "adversarial-critic",
    "code-quality",
    "refactorer",
]


class ProviderConfig(BaseModel):
    name: str
    base_url: str
    api_key_env: str
    api_key: str = ""  # populated by loader after env resolution


class MemberConfig(BaseModel):
    name: str
    model: str
    role: Role
    timeout: int = Field(gt=0, le=600)
    provider: str | None = None               # resolved to providers[0].name if None
    fallback_provider: str | None = None
    fallback_model: str | None = None


class CartographerConfig(BaseModel):
    model: str
    timeout: int = Field(default=120, gt=0, le=600)
    provider: str | None = None               # defaults to providers[0].name


class LimitsConfig(BaseModel):
    max_files_scan: int = Field(default=50, gt=0, le=500)
    max_additional_file_requests: int = Field(default=3, ge=0, le=20)
    debate_timeout: int = Field(default=180, gt=0, le=1800)
    per_member_timeout_fallback: int = Field(default=90, gt=0, le=600)
    cache_ttl_seconds: int = Field(default=600, ge=0, le=86400)
    max_file_size_bytes: int = Field(default=51200, gt=0, le=5_000_000)


class Config(BaseModel):
    providers: list[ProviderConfig]
    members: list[MemberConfig]
    cartographer: CartographerConfig
    limits: LimitsConfig

    @model_validator(mode="after")
    def _validate(self) -> "Config":
        if not self.providers:
            raise ValueError("at least one provider is required")
        names = [p.name for p in self.providers]
        if len(set(names)) != len(names):
            raise ValueError("provider names must be unique")
        known = set(names)

        if len(self.members) != 5:
            raise ValueError("council must have exactly 5 members")
        mnames = [m.name for m in self.members]
        if len(set(mnames)) != len(mnames):
            raise ValueError("member names must be unique")

        default_provider = self.providers[0].name
        for m in self.members:
            if m.provider is None:
                m.provider = default_provider
            if m.provider not in known:
                raise ValueError(f"unknown provider '{m.provider}' on member '{m.name}'")
            if m.fallback_provider is not None:
                if m.fallback_provider not in known:
                    raise ValueError(
                        f"unknown provider '{m.fallback_provider}' in fallback_provider on member '{m.name}'"
                    )
                if m.fallback_provider == m.provider:
                    raise ValueError(
                        f"member '{m.name}' fallback_provider is the same provider as primary"
                    )
                if not m.fallback_model:
                    raise ValueError(
                        f"member '{m.name}' sets fallback_provider but no fallback_model"
                    )
            elif m.fallback_model is not None:
                raise ValueError(
                    f"member '{m.name}' sets fallback_model without fallback_provider"
                )

        if self.cartographer.provider is None:
            self.cartographer.provider = default_provider
        elif self.cartographer.provider not in known:
            raise ValueError(f"unknown provider '{self.cartographer.provider}' in cartographer")
        return self


def _back_compat_providers(data: dict) -> list[dict]:
    """If [[providers]] is missing but legacy [openrouter] is present, synthesize one."""
    if "providers" in data and data["providers"]:
        return data["providers"]
    legacy = data.get("openrouter") or {}
    base_url = legacy.get("base_url", "https://openrouter.ai/api/v1")
    return [{"name": "openrouter", "base_url": base_url, "api_key_env": "OPENROUTER_API_KEY"}]


def load_config(toml_path: Path) -> Config:
    load_dotenv()

    if not toml_path.exists():
        raise ConfigError(f"Council config not found: {toml_path}")

    try:
        with toml_path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {toml_path}: {exc}") from exc

    # Back-compat: synthesize providers if needed
    providers_data = _back_compat_providers(data)
    # Resolve api keys from environment
    resolved: list[dict] = []
    for p in providers_data:
        env_name = p.get("api_key_env", "OPENROUTER_API_KEY")
        key = os.getenv(env_name)
        if not key:
            raise ConfigError(f"{env_name} is not set in environment or .env")
        resolved.append({**p, "api_key": key})
    data = {**data, "providers": resolved}
    data.pop("openrouter", None)  # ignore legacy block when providers are present

    try:
        return Config(**data)
    except Exception as exc:
        msg = str(exc)
        raise ConfigError(msg) from exc


def provider_by_name(cfg: Config, name: str) -> ProviderConfig:
    for p in cfg.providers:
        if p.name == name:
            return p
    raise ConfigError(f"provider not found: {name}")
```

- [ ] **Step 4: Run config tests**

Run: `pytest tests/test_config.py -v`
Expected: all PASS. If a pre-existing test accessed `cfg.api_key` or `cfg.openrouter.base_url` directly, update it to use `cfg.providers[0].api_key` / `cfg.providers[0].base_url`.

- [ ] **Step 5: Commit**

```bash
git add src/anti_hacker/config.py tests/test_config.py
git commit -m "feat(config): provider registry with per-member fallback fields"
```

---

## Task 4: Update `CouncilMember` to hold primary + fallback and return structured replies

**Files:**
- Modify: `src/anti_hacker/council/member.py`
- Create: `tests/test_member_fallback.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_member_fallback.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
import pytest

from anti_hacker.config import MemberConfig
from anti_hacker.council.member import CouncilMember, MemberReply
from anti_hacker.errors import OpenRouterError


@dataclass
class FakeResp:
    text: str
    model: str


class FakeClient:
    def __init__(self, *, raises: Exception | None = None, text: str = "ok"):
        self.raises = raises
        self.text = text
        self.calls: list[dict] = []

    async def chat(self, *, model, system, user, timeout, max_retries=None, **_):
        self.calls.append({"model": model, "timeout": timeout, "max_retries": max_retries})
        if self.raises is not None:
            raise self.raises
        return FakeResp(text=self.text, model=model)


def _cfg(**overrides) -> MemberConfig:
    base = dict(
        name="m1",
        model="primary/model",
        role="security-paranoid",
        timeout=30,
        provider="openrouter",
    )
    base.update(overrides)
    return MemberConfig(**base)


@pytest.mark.asyncio
async def test_primary_success_no_fallback_call():
    primary = FakeClient(text="primary-ok")
    fallback = FakeClient(text="fallback-ok")
    member = CouncilMember(
        config=_cfg(fallback_provider="modal", fallback_model="fb/model"),
        primary_client=primary,
        fallback_client=fallback,
    )
    reply = await member.ask(system="s", user="u")
    assert isinstance(reply, MemberReply)
    assert reply.text == "primary-ok"
    assert reply.provider == "openrouter"
    assert reply.via_fallback is False
    assert len(primary.calls) == 1
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_rate_limit_triggers_fallback():
    primary = FakeClient(raises=OpenRouterError("limit", kind="rate_limit"))
    fallback = FakeClient(text="fallback-ok")
    member = CouncilMember(
        config=_cfg(fallback_provider="modal", fallback_model="fb/model"),
        primary_client=primary,
        fallback_client=fallback,
    )
    reply = await member.ask(system="s", user="u")
    assert reply.text == "fallback-ok"
    assert reply.provider == "modal"
    assert reply.via_fallback is True
    assert fallback.calls[0]["model"] == "fb/model"
    assert fallback.calls[0]["max_retries"] == 1


@pytest.mark.asyncio
async def test_timeout_does_not_trigger_fallback():
    primary = FakeClient(raises=OpenRouterError("slow", kind="timeout"))
    fallback = FakeClient(text="unused")
    member = CouncilMember(
        config=_cfg(fallback_provider="modal", fallback_model="fb/model"),
        primary_client=primary,
        fallback_client=fallback,
    )
    with pytest.raises(OpenRouterError) as ei:
        await member.ask(system="s", user="u")
    assert ei.value.kind == "timeout"
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_rate_limit_no_fallback_configured_propagates():
    primary = FakeClient(raises=OpenRouterError("limit", kind="rate_limit"))
    member = CouncilMember(
        config=_cfg(),  # no fallback
        primary_client=primary,
        fallback_client=None,
    )
    with pytest.raises(OpenRouterError):
        await member.ask(system="s", user="u")


@pytest.mark.asyncio
async def test_rate_limit_fallback_also_fails_propagates():
    primary = FakeClient(raises=OpenRouterError("limit", kind="rate_limit"))
    fallback = FakeClient(raises=OpenRouterError("fb-limit", kind="rate_limit"))
    member = CouncilMember(
        config=_cfg(fallback_provider="modal", fallback_model="fb/model"),
        primary_client=primary,
        fallback_client=fallback,
    )
    with pytest.raises(OpenRouterError):
        await member.ask(system="s", user="u")
```

- [ ] **Step 2: Run to confirm failures**

Run: `pytest tests/test_member_fallback.py -v`
Expected: FAIL — `CouncilMember` signature mismatch, `MemberReply` missing.

### Step 3: Implement new `CouncilMember` and `MemberReply`

- [ ] Replace `src/anti_hacker/council/member.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..config import MemberConfig, Role
from ..errors import OpenRouterError


class ChatClient(Protocol):
    async def chat(
        self,
        *,
        model: str,
        system: str,
        user: str,
        timeout: float,
        max_retries: int | None = None,
        response_format_json: bool = True,
    ) -> Any: ...


@dataclass(frozen=True)
class MemberReply:
    text: str
    provider: str
    via_fallback: bool
    model: str


@dataclass
class CouncilMember:
    config: MemberConfig
    primary_client: ChatClient
    fallback_client: ChatClient | None = None

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def role(self) -> Role:
        return self.config.role

    async def ask(self, *, system: str, user: str) -> MemberReply:
        timeout = float(self.config.timeout)
        try:
            resp = await self.primary_client.chat(
                model=self.config.model,
                system=system,
                user=user,
                timeout=timeout,
            )
            return MemberReply(
                text=resp.text,
                provider=self.config.provider or "",
                via_fallback=False,
                model=self.config.model,
            )
        except OpenRouterError as exc:
            if (
                exc.kind == "rate_limit"
                and self.fallback_client is not None
                and self.config.fallback_provider is not None
                and self.config.fallback_model is not None
            ):
                fb_resp = await self.fallback_client.chat(
                    model=self.config.fallback_model,
                    system=system,
                    user=user,
                    timeout=timeout,
                    max_retries=1,
                )
                return MemberReply(
                    text=fb_resp.text,
                    provider=self.config.fallback_provider,
                    via_fallback=True,
                    model=self.config.fallback_model,
                )
            raise
```

- [ ] **Step 4: Run fallback tests**

Run: `pytest tests/test_member_fallback.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/anti_hacker/council/member.py tests/test_member_fallback.py
git commit -m "feat(council): per-member fallback dispatch on rate_limit errors"
```

---

## Task 5: Teach `DebateOrchestra` about `MemberReply` and record provider meta

**Files:**
- Modify: `src/anti_hacker/council/orchestra.py`
- Test: `tests/test_orchestra.py`

- [ ] **Step 1: Update `_ask_all` to consume `MemberReply`**

Current `_ask_all` calls `await member.ask(...)` and treats the return as a string. Update it to accept `MemberReply` and capture meta.

Add to `RoundResult`:

```python
member_meta: dict[int, dict[str, dict[str, Any]]] = field(default_factory=dict)
# shape: {round_number: {member_name: {"provider": str, "via_fallback": bool, "model": str}}}
```

In `_run_inner`, before each `_ask_all` call, pass the round number so meta gets bucketed:

```python
r1 = await self._ask_all(active, r1_user, result, round_number=1)
...
r2 = await self._ask_all(active, r2_user, result, round_number=2)
...
r3 = await self._ask_all(active, r3_user, result, round_number=3)
```

In `_ask_all`, change the signature and inner `_one`:

```python
async def _ask_all(
    self,
    members: list[CouncilMember],
    user_prompt: str,
    result: RoundResult,
    *,
    round_number: int,
) -> dict[str, dict[str, Any]]:
    async def _one(member: CouncilMember) -> tuple[str, dict[str, Any] | None, str, dict[str, Any] | None]:
        system = role_system_prompt(member.role)
        reply_meta: dict[str, Any] | None = None
        try:
            reply = await member.ask(system=system, user=user_prompt)
            reply_meta = {
                "provider": reply.provider,
                "via_fallback": reply.via_fallback,
                "model": reply.model,
            }
            raw = reply.text
        except OpenRouterError as exc:
            return member.name, None, f"openrouter: {exc}", None
        ok, payload, err = parse_member_json(raw)
        if not ok:
            try:
                reply2 = await member.ask(
                    system=system,
                    user=REPAIR_INSTRUCTION.format(raw=raw[:500]),
                )
                reply_meta = {
                    "provider": reply2.provider,
                    "via_fallback": reply2.via_fallback,
                    "model": reply2.model,
                }
                raw2 = reply2.text
            except OpenRouterError as exc:
                return member.name, None, f"openrouter(repair): {exc}", None
            ok2, payload2, err2 = parse_member_json(raw2)
            if not ok2:
                return member.name, None, f"invalid_json: {err2}", None
            return member.name, payload2, "", reply_meta
        return member.name, payload, "", reply_meta

    tasks = [asyncio.create_task(_one(m)) for m in members]
    out: dict[str, dict[str, Any]] = {}
    meta_bucket = result.member_meta.setdefault(round_number, {})
    for coro in asyncio.as_completed(tasks):
        name, payload, err, meta = await coro
        if payload is None:
            if name not in result.abstained:
                result.abstained.append(name)
            result.errors[name] = err
            logger.warning("member %s abstained: %s", name, err)
        else:
            out[name] = payload
            if meta is not None:
                meta_bucket[name] = meta
    return out
```

- [ ] **Step 2: Add test for meta propagation**

Append to `tests/test_orchestra.py` (or adapt an existing fixture — inspect the file for a helper that builds `CouncilMember` with a fake client):

```python
@pytest.mark.asyncio
async def test_member_meta_recorded_per_round(monkeypatch):
    # Build a minimal orchestra with one member whose primary returns JSON;
    # assert result.member_meta[1]["m1"] contains provider/via_fallback/model.
    # (Reuse helpers already in this file for member construction.)
    ...
```

Fill in the body using the helper the file already uses to build a faked `CouncilMember`. If none exists, build a thin fake inline that mirrors `test_member_fallback.py`'s `FakeClient`, wrap in `CouncilMember(config=..., primary_client=fake, fallback_client=None)`, run a round, and assert on `result.member_meta`.

- [ ] **Step 3: Update existing orchestra tests**

Inspect `tests/test_orchestra.py` for any fake `member` that returns a bare string. Update the fake's `ask` to return `MemberReply(text=..., provider="openrouter", via_fallback=False, model=member.config.model)` so it satisfies the new contract.

- [ ] **Step 4: Run orchestra tests**

Run: `pytest tests/test_orchestra.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/anti_hacker/council/orchestra.py tests/test_orchestra.py
git commit -m "feat(orchestra): record provider/via_fallback meta per round"
```

---

## Task 6: Persist `member_meta` in the debate log

**Files:**
- Modify: `src/anti_hacker/io/debate_log.py`
- Modify: `src/anti_hacker/tools/consult.py`
- Test: `tests/test_debate_log.py`, `tests/test_tools_consult.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_debate_log.py`:

```python
def test_record_round_with_meta(tmp_path):
    from anti_hacker.io.debate_log import DebateLog
    log = DebateLog(debate_id="d1", root=tmp_path)
    log.record_round(
        1,
        {"m1": {"verdict": "ok"}},
        meta={"m1": {"provider": "modal", "via_fallback": True, "model": "fb/m"}},
    )
    path = log.finalize({"verdict": "CLEAN"})
    import json
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["rounds"][0]["member_meta"]["m1"]["via_fallback"] is True
    assert payload["rounds"][0]["member_meta"]["m1"]["provider"] == "modal"
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/test_debate_log.py::test_record_round_with_meta -v`
Expected: FAIL — `record_round` does not accept `meta`.

- [ ] **Step 3: Update `DebateLog.record_round`**

In `src/anti_hacker/io/debate_log.py`:

```python
def record_round(
    self,
    round_number: int,
    responses: dict[str, Any],
    meta: dict[str, Any] | None = None,
) -> None:
    entry: dict[str, Any] = {
        "round": round_number,
        "at": datetime.now(timezone.utc).isoformat(),
        "responses": responses,
    }
    if meta is not None:
        entry["member_meta"] = meta
    self._rounds.append(entry)
```

- [ ] **Step 4: Feed meta from `ConsultService`**

In `src/anti_hacker/tools/consult.py`, replace the three `log.record_round(N, result.roundN)` calls with:

```python
log.record_round(1, result.round1, meta=result.member_meta.get(1))
log.record_round(2, result.round2, meta=result.member_meta.get(2))
log.record_round(3, result.round3, meta=result.member_meta.get(3))
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_debate_log.py tests/test_tools_consult.py -v`
Expected: all PASS. Adjust any assertions in `test_tools_consult.py` that snapshot the exact rounds structure to tolerate the new optional `member_meta` key.

- [ ] **Step 6: Commit**

```bash
git add src/anti_hacker/io/debate_log.py src/anti_hacker/tools/consult.py tests/test_debate_log.py tests/test_tools_consult.py
git commit -m "feat(log): persist per-member provider and via_fallback metadata"
```

---

## Task 7: Wire up provider registry in `server.py` and `consult.py`

**Files:**
- Modify: `src/anti_hacker/server.py`
- Modify: `src/anti_hacker/tools/consult.py`
- Modify: `src/anti_hacker/scanners/cartographer.py` (only if it currently takes a single client — confirm by reading)

- [ ] **Step 1: Build a `clients` dict from the registry**

In `src/anti_hacker/server.py`, replace the block:

```python
client = OpenRouterClient(api_key=cfg.api_key, base_url=cfg.openrouter.base_url)
```

with:

```python
clients: dict[str, OpenRouterClient] = {
    p.name: OpenRouterClient(api_key=p.api_key, base_url=p.base_url)
    for p in cfg.providers
}
```

Update every downstream constructor that took a single `client=` to take `clients=clients` instead:

```python
consult = ConsultService(config=cfg, clients=clients, cache=cache, project_root=project_root, data_root=data_root)
cart_client = clients[cfg.cartographer.provider or cfg.providers[0].name]
cart = Cartographer(client=cart_client, model=cfg.cartographer.model, timeout=cfg.cartographer.timeout)
```

- [ ] **Step 2: Update `ConsultService`**

In `src/anti_hacker/tools/consult.py`:

1. Change `__init__` signature:

```python
def __init__(
    self,
    *,
    config: Config,
    clients: dict[str, OpenRouterClient],
    cache: DebateCache,
    project_root: Path,
    data_root: Path,
) -> None:
    self.config = config
    self.clients = clients
    ...
```

2. Replace the member construction inside `consult()`:

```python
members: list[CouncilMember] = []
for mc in self.config.members:
    primary = self.clients[mc.provider]  # validator guarantees presence
    fallback = self.clients[mc.fallback_provider] if mc.fallback_provider else None
    members.append(
        CouncilMember(
            config=mc,
            primary_client=primary,
            fallback_client=fallback,
        )
    )
```

- [ ] **Step 3: Update tests that construct `ConsultService`**

Inspect `tests/test_tools_consult.py` and update any `ConsultService(client=...)` construction to `ConsultService(clients={"openrouter": ...})`. Where a fake `MemberConfig` is built, ensure it has `provider="openrouter"`.

- [ ] **Step 4: Run the full suite**

Run: `pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/anti_hacker/server.py src/anti_hacker/tools/consult.py tests/test_tools_consult.py
git commit -m "feat(wiring): instantiate one client per provider and dispatch to members"
```

---

## Task 8: Migrate `config/council.toml` and document `MODAL_API_KEY`

**Files:**
- Modify: `config/council.toml`
- Modify: `README.md` (or `.env.example` if one exists)

- [ ] **Step 1: Rewrite `config/council.toml`**

Replace the top-level `[openrouter]` section with:

```toml
[[providers]]
name = "openrouter"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"

[[providers]]
name = "modal"
base_url = "https://api.us-west-2.modal.direct/v1"
api_key_env = "MODAL_API_KEY"
```

Add `fallback_provider` / `fallback_model` to each `[[members]]` block the user wants covered. Leave the rest alone. Example for one member:

```toml
[[members]]
name = "trinity-large"
provider = "openrouter"
model    = "arcee-ai/trinity-large-preview:free"
role     = "security-paranoid"
timeout  = 120
fallback_provider = "modal"
fallback_model    = "zai-org/GLM-5.1-FP8"
```

The user must confirm which Modal models to map to which member before the migration is final; keep the mapping in a single commit for easy review.

- [ ] **Step 2: Document `MODAL_API_KEY`**

Find the env-setup section in `README.md`. Add:

```
MODAL_API_KEY=<your key from modal.direct dashboard>
```

next to the existing `OPENROUTER_API_KEY` line. If an `.env.example` file exists, append the same line there (do NOT commit real keys).

- [ ] **Step 3: Start the server and run smoke**

Run: `python -m anti_hacker` (or the equivalent entrypoint) to confirm config loads. Kill immediately.

If there's `scripts/smoke.py`, run it with both providers live:

```bash
python scripts/smoke.py
```

Expected: existing smoke test still passes with the new config.

- [ ] **Step 4: Commit**

```bash
git add config/council.toml README.md
git commit -m "chore(config): migrate to provider registry and add Modal fallback"
```

---

## Task 9: (Optional) Add a Modal-only smoke check

**Files:**
- Modify: `scripts/smoke.py` (only if the script exists)

- [ ] **Step 1: Add a flag-gated Modal probe**

Locate `scripts/smoke.py`. Add a new CLI flag `--include-modal`. When set, make a single `chat()` call against the `modal` provider using the first member's `fallback_model` (or a fixed known-good model like `zai-org/GLM-5.1-FP8`). Print the provider/model and the first 80 chars of the response.

- [ ] **Step 2: Run it manually**

```bash
python scripts/smoke.py --include-modal
```

Expected: one successful Modal call, one non-zero-length response.

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke.py
git commit -m "test(smoke): flag-gated Modal provider probe"
```

---

## Self-Review Notes

- Spec coverage: providers registry (Tasks 3, 7), per-member fallback config (Task 3), `kind` classification (Tasks 1–2), fallback dispatch (Task 4), single fallback attempt (Task 4 via `max_retries=1`), provider/via_fallback logging (Tasks 5–6), back-compat (Task 3), `MODAL_API_KEY` documentation (Task 8), Modal smoke (Task 9). All covered.
- Types: `MemberReply` defined in Task 4, consumed in Task 5, persisted in Task 6 — field names `text`, `provider`, `via_fallback`, `model` consistent throughout.
- `ChatClient` Protocol introduced in Task 4; `OpenRouterClient` satisfies it structurally because Task 2 added the matching `max_retries` kwarg.
- No placeholders; every code step ships the actual code or exact edits.
