# Ollama Cloud Provider + Multi-Step Fallback Chain

**Date:** 2026-04-19
**Status:** Draft — awaiting user review
**Related:** `2026-04-16-anti-hacker-council-design.md` (original registry + single-level fallback)

## Problem

1. **Auto-switch bug.** On exhausted OpenRouter free-tier quota, OpenRouter frequently returns `HTTP 200` with empty `choices`/`content` instead of `429`. The current `OpenRouterClient` classifies this as `kind="malformed"`, and `CouncilMember.ask` only triggers a fallback on `kind="rate_limit"`. Result: the member abstains instead of switching to Modal, and the user observes "OpenRouter keeps being called even when quota is dead."
2. **Single-level fallback.** `MemberConfig` has only one `fallback_provider`/`fallback_model`. If that fallback is also down, the member abstains.
3. **Ollama Cloud not reachable.** The user is logged into the Ollama Desktop app (`minimax-m2.7:cloud` available locally via `http://localhost:11434/v1`), but the provider registry has no entry for it.

## Goals

- Correctly detect OpenRouter quota exhaustion and trigger fallback automatically.
- Support a **chain** of fallbacks per member (try next on failure, not give up after one).
- Register `ollama` as a third provider alongside `openrouter` and `modal`.
- Distribute the chain across members to preserve council diversity in worst-case outages (raskla B).
- Preserve backward compatibility with existing single-fallback configs.

## Non-Goals

- Adding Ollama as a primary provider for any member (cloud-free-tier quotas unknown; keep as reserve).
- Auto-starting the Ollama Desktop app if it is not running — if Ollama is unreachable, treat as any other network error and move to next fallback.
- Changing the trigger condition beyond quota/rate-limit: `timeout`, `upstream 5xx`, `network`, `malformed` on Modal/Ollama still abstain (user chose option A for trigger).

## Design

### 1. Empty-200 quota detection

New `kind="quota_exhausted"` in `OpenRouterError`. Added per-provider flag `empty_means_quota: bool = False` in `ProviderConfig`.

`OpenRouterClient.__init__` accepts a new `empty_means_quota: bool = False` argument, wired from `ProviderConfig.empty_means_quota` at instantiation in `server.py`.

In the client's `status==200` branch (currently `client.py:89-103`):

- First empty `choices`/`content` while `use_json_mode=True` still flips to non-json-mode and retries (unchanged — some free models reject `response_format`).
- Second empty after the retry, **if `self._empty_means_quota`** → raise `OpenRouterError(kind="quota_exhausted")`. Otherwise → `kind="malformed"` as today.

`config/council.toml` sets `empty_means_quota = true` on the `openrouter` provider; `modal` and `ollama` leave it at default `false`.

### 2. Fallback chain

**Schema change (`config.py`):**

```python
class FallbackEntry(BaseModel):
    provider: str
    model: str

class MemberConfig(BaseModel):
    ...
    fallbacks: list[FallbackEntry] = []
    # legacy, still accepted but deprecated:
    fallback_provider: str | None = None
    fallback_model: str | None = None
```

**Back-compat loader:** in `_validate`, if `fallbacks` is empty and legacy `fallback_provider`/`fallback_model` are set, synthesize `fallbacks = [FallbackEntry(provider=..., model=...)]`. If both are set, raise `ConfigError`. After synthesis, legacy fields are ignored.

**Validation:** every `entry.provider` must exist in `providers`; no entry's provider may equal `member.provider`; adjacent entries may repeat providers with different models (allowed); `entry.model` must be non-empty.

### 3. Ollama provider

Added to `ProviderConfig`:

```python
class ProviderConfig(BaseModel):
    name: str
    base_url: str
    api_key_env: str | None = None   # was required; now optional
    api_key: str = ""
    empty_means_quota: bool = False
```

Loader (`config.py::load_config`):

- If `api_key_env is None`: set `api_key = ""` (Ollama accepts any Bearer token including empty).
- If `api_key_env` is set but env var is missing: raise `ConfigError` (unchanged).

`config/council.toml` gains:

```toml
[[providers]]
name = "ollama"
base_url = "http://localhost:11434/v1"
# api_key_env omitted — local Ollama needs no key
empty_means_quota = false
```

### 4. Member chain logic

Replace `CouncilMember.ask` single-fallback block with a loop:

```python
async def ask(self, *, system: str, user: str) -> MemberReply:
    timeout = float(self.config.timeout)
    try:
        resp = await self.primary_client.chat(
            model=self.config.model, system=system, user=user, timeout=timeout,
        )
        return MemberReply(resp.text, self.config.provider, False, self.config.model)
    except OpenRouterError as exc:
        if exc.kind not in {"rate_limit", "quota_exhausted"}:
            raise
        last = exc
        for entry, client in self.fallback_chain:   # zip of FallbackEntry + ChatClient
            try:
                resp = await client.chat(
                    model=entry.model, system=system, user=user,
                    timeout=timeout, max_retries=1,
                )
                return MemberReply(resp.text, entry.provider, True, entry.model)
            except OpenRouterError as exc2:
                if exc2.kind in {"rate_limit", "quota_exhausted"}:
                    last = exc2
                    continue          # try next link in the chain
                raise                 # timeout/upstream/malformed/network → abstain
        raise last    # chain exhausted — re-raise last rate_limit/quota
```

`CouncilMember.fallback_chain: list[tuple[FallbackEntry, ChatClient]]` is built in `server.py` during member construction by resolving each `FallbackEntry.provider` to its `OpenRouterClient`.

### 5. Chain distribution (option B)

`config/council.toml` member chains:

| Member           | Primary       | Fallback 1               | Fallback 2                       |
|------------------|---------------|--------------------------|----------------------------------|
| trinity-large    | openrouter    | modal (GLM-5-FP8-2)      | ollama (minimax-m2.7:cloud)      |
| glm-4.5-air      | openrouter    | ollama (minimax-m2.7:cloud) | modal (GLM-5-FP8-2)           |
| gpt-oss-120b     | openrouter    | modal (GLM-5-FP8-2)      | ollama (minimax-m2.7:cloud)      |
| nemotron-nano    | openrouter    | ollama (minimax-m2.7:cloud) | modal (GLM-5-FP8-2)           |
| nemotron-super   | openrouter    | modal (GLM-5-FP8-2)      | ollama (minimax-m2.7:cloud)      |

If OpenRouter globally dies and Modal is also rate-limited, three members reach `ollama-minimax` first and two reach it second — worst case is 5 × minimax, but the shuffled chain means partial outages still preserve 2–3 distinct voices.

## Testing

- **Unit:** new test in `tests/openrouter/test_client.py` — empty-200 with `empty_means_quota=True` raises `kind="quota_exhausted"`; with default `False` raises `kind="malformed"` (regression guard).
- **Unit:** new test in `tests/council/test_member.py` — chain of 2 fallbacks; primary raises `quota_exhausted`, fallback[0] raises `rate_limit`, fallback[1] succeeds → reply comes from fallback[1], `via_fallback=True`, `provider == entry[1].provider`.
- **Unit:** chain with primary `malformed` → no fallback attempted, re-raises (abstain preserved).
- **Unit:** config back-compat — legacy `fallback_provider`/`fallback_model` still loads, produces one-element `fallbacks`.
- **Unit:** config validation — Ollama provider with no `api_key_env` loads cleanly with `api_key = ""`.
- **Smoke (flag-gated, as existing `_staya_staging/smoke_*`):** script that hits the real Ollama local endpoint with `minimax-m2.7:cloud`, verifies a reply. Gated behind `ANTI_HACKER_SMOKE_OLLAMA=1` so CI on machines without Ollama skips it.

## Rollout / Migration

- Existing `council.toml` keeps working unchanged (back-compat loader).
- After merge, user edits `config/council.toml` to add the Ollama provider block and switch members to the new `fallbacks = [...]` array using the table above.
- No `.env` changes required for Ollama (no API key).

## Risks

- **Ollama Desktop not running** when user audits → `httpx.ConnectError` → `kind="network"` → abstain on that link → chain moves to next. Acceptable, no UX change needed.
- **Minimax-m2.7:cloud quota on Ollama Cloud** is not publicly documented for the free tier. If it returns empty-200 on quota, we will not detect it (`empty_means_quota=false` for ollama). Acceptable for now — revisit if observed in practice; the flag is per-provider, so only that line in `council.toml` would flip.
- **Minor increase in worst-case latency** — at most two extra chat round-trips per member when the chain unwinds fully. Bounded by `member.timeout × (1 + len(fallbacks))` which still fits inside `limits.debate_timeout` at current values.

## Out of Scope (for this spec)

- Per-fallback timeout override (uses member's primary timeout for now).
- Circuit breaker to temporarily skip a known-dead provider across members (stateful; separate effort).
- Making Ollama the primary provider for any member.
