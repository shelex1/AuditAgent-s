# Modal Provider Fallback — Design

**Date:** 2026-04-17
**Status:** Approved (brainstorm)
**Follows:** `2026-04-16-anti-hacker-council-design.md`

## Goal

Add a second LLM provider (Modal Research) alongside OpenRouter so that individual council members can fall back to Modal when their OpenRouter quota is exhausted. OpenRouter stays the primary; Modal is opt-in per member.

## Non-goals

- Automatic provider selection by price/latency.
- Dynamic provider discovery.
- Sticky fallback state across rounds or debates (each call retries primary first).
- Migrating all 5 members to Modal — user chooses per member in config.
- Renaming the `openrouter/` module or `OpenRouterClient` class (they are already
  OpenAI-compatible; rename is cosmetic churn and out of scope).

## Decisions

All decisions below were settled during the 2026-04-17 brainstorm.

1. **Provider Registry** (not a dual-client wrapper, not env-only fields). Providers
   are named first-class entities in `council.toml`; members reference them by name.
2. **Per-member fallback granularity.** Each `[[members]]` entry may declare an
   optional `fallback_provider` + `fallback_model`. Other members on the same
   debate are unaffected.
3. **Trigger: quota/rate-limit only.** Fallback fires only when the primary's
   full retry cycle ended on a 429 or explicit quota signal. Timeouts, 5xx,
   malformed responses, and network errors still result in `abstained` (no
   fallback). Rationale: a different model on a different provider is equally
   likely to time out; fallback exists strictly to route around exhausted quota.
4. **Fallback is a single attempt.** The fallback call uses `max_retries=1`
   (no retry schedule). If it fails for any reason, the member abstains.
5. **OpenAI-compatible reuse.** Modal exposes `POST /v1/chat/completions` with
   `Authorization: Bearer <key>` — identical shape to OpenRouter. The existing
   `OpenRouterClient` is used as-is, instantiated once per provider.

## Architecture

### Provider registry

At startup, `load_config` parses `[[providers]]` entries from TOML, resolves
each provider's API key via its `api_key_env` (e.g. `OPENROUTER_API_KEY`,
`MODAL_API_KEY`), and exposes a `providers: list[ProviderConfig]` on the
`Config` object.

The orchestrator (or `server.py` wiring layer) builds `clients: dict[str, ChatClient]`
keyed by provider name — one `OpenRouterClient` instance per provider, each with
its own `base_url` and `api_key`.

### Member wiring

Each `CouncilMember` is constructed with:

- `primary_client: ChatClient`
- `primary_model: str`
- `fallback_client: ChatClient | None`
- `fallback_model: str | None`

The debate orchestration loop is unchanged — it still calls `member.ask(...)`
and treats exceptions as `abstained`.

### Fallback logic inside `CouncilMember.ask`

```
try:
    return await primary_client.chat(primary_model, ..., timeout=member_timeout)
except OpenRouterError as exc:
    if exc.kind == "rate_limit" and fallback_client is not None:
        return await fallback_client.chat(
            fallback_model, ..., timeout=member_timeout, max_retries=1
        )
    raise
```

`max_retries=1` is passed per-call (new kwarg on `chat()`), overriding the
client-level default for this single fallback attempt. Client-level default
stays at 3 for the primary path.

### Error classification

`OpenRouterError` gains an optional `kind` field:

```python
Kind = Literal["rate_limit", "timeout", "upstream", "malformed", "network"]
```

The client sets `kind` based on the terminal failure of the retry loop:

- `rate_limit` — last attempt was HTTP 429, OR the exception message matches a
  quota whitelist (`"rate limit"`, `"quota"`, `"insufficient_quota"`, case-insensitive).
- `timeout` — last attempt was `httpx.TimeoutException`.
- `upstream` — last attempt was HTTP 5xx.
- `malformed` — JSON/schema parse failure.
- `network` — other `httpx.HTTPError`.

Only `rate_limit` triggers fallback in `CouncilMember.ask`.

## Configuration

### `.env` (gitignored)

```
OPENROUTER_API_KEY=sk-or-...
MODAL_API_KEY=modalresearch_...
```

### `config/council.toml` (gitignored)

```toml
[[providers]]
name = "openrouter"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"

[[providers]]
name = "modal"
base_url = "https://api.us-west-2.modal.direct/v1"
api_key_env = "MODAL_API_KEY"

[[members]]
name = "trinity-large"
provider = "openrouter"            # optional; defaults to the first provider in the registry
model    = "arcee-ai/trinity-large-preview:free"
role     = "security-paranoid"
timeout  = 120
fallback_provider = "modal"         # optional
fallback_model    = "zai-org/GLM-5.1-FP8"  # required iff fallback_provider is set

# ... 4 more members, each may or may not set fallback_*
```

### Validation (Pydantic)

- `providers`: length ≥ 1; `name` unique across the list; each `api_key_env`
  must resolve to a non-empty environment variable at load time.
- `members.provider` (if set) must reference an existing provider name; if
  omitted, defaults to `providers[0].name`.
- `members.fallback_provider` (if set) must reference an existing provider name.
- `members.fallback_model` is required iff `fallback_provider` is set, and
  forbidden otherwise.
- `fallback_provider != provider` (rejecting same-provider fallback as a config
  error — it provides no value).

### Backward compatibility

The old top-level `[openrouter] base_url = ...` block is still accepted for one
or two releases. When detected and no `[[providers]]` is present, the loader
synthesizes a single provider:

```
name = "openrouter"
base_url = <from old [openrouter].base_url>
api_key_env = "OPENROUTER_API_KEY"
```

If both `[openrouter]` and `[[providers]]` are present, `[[providers]]` wins
and a deprecation warning is logged.

## Logging

`debates/*.json` already records `model` per member reply. Two fields are added:

- `provider: str` — the provider name actually used for the reply.
- `via_fallback: bool` — `true` iff the primary raised `rate_limit` and the
  reply came from the fallback path.

The compact verdict returned to Claude is unchanged (keeps the token budget).

## Affected files

| File | Change |
|------|--------|
| `src/anti_hacker/config.py` | Add `ProviderConfig`; add `provider`, `fallback_provider`, `fallback_model` to `MemberConfig`; resolve `api_key_env` → key; add validators; keep back-compat shim for `[openrouter]`. |
| `src/anti_hacker/openrouter/client.py` | Add `kind` field to `OpenRouterError`; set it at every terminal raise point; add `max_retries` kwarg to `chat()` overriding the instance default for a single call; update class docstring to state "any OpenAI-compatible endpoint". |
| `src/anti_hacker/council/member.py` | Hold primary + optional fallback client/model; implement fallback dispatch in `ask()`. |
| `src/anti_hacker/council/orchestra.py` (or `server.py`) | Build `dict[str, ChatClient]` from the provider registry at startup; pass primary/fallback pair to each `CouncilMember`; record `provider` and `via_fallback` in debate log entries. |
| `config/council.toml` | Migrate to `[[providers]]`; users opt in to `fallback_*` per member as desired. |
| `.env.example` (if present) or README | Add `MODAL_API_KEY`. |

## Tests

- `test_config.py`
  - Parses `[[providers]]` registry correctly.
  - Defaults `member.provider` to the first provider when omitted.
  - Rejects `fallback_provider` that doesn't exist in registry.
  - Rejects `fallback_model` without `fallback_provider`, and vice versa.
  - Rejects `fallback_provider == provider`.
  - Errors when `api_key_env` is not set in environment.
  - Back-compat shim: old `[openrouter]` block still loads.

- `test_client.py`
  - `OpenRouterError.kind` is `rate_limit` after 429 retries exhausted.
  - `OpenRouterError.kind` is `timeout` / `upstream` / `malformed` / `network`
    on respective terminal failures.
  - `chat(max_retries=1)` makes exactly one attempt regardless of instance default.

- `test_member_fallback.py` (new)
  - 429 on primary → fallback is called with `fallback_model` and `max_retries=1`, returns its response.
  - Non-quota error on primary → fallback is NOT called; error propagates.
  - 429 on primary, fallback also fails → exception propagates (member will abstain).
  - No fallback configured + 429 on primary → exception propagates (member abstains).

- Smoke test (`scripts/smoke.py` or equivalent): gated behind a flag (`--include-modal`), a single direct call to Modal to confirm the key and endpoint. Off by default to conserve quota in CI.

## Rollout

1. Land code + tests on a branch.
2. User rotates leaked Modal key, puts the new one in `.env`.
3. User updates `config/council.toml` to the `[[providers]]` format and opts
   any subset of members into `fallback_*` (or none — opt-in).
4. Run `scripts/smoke.py --include-modal` once manually to verify.
5. Merge.

## Open questions

None — all brainstorm decisions resolved.
