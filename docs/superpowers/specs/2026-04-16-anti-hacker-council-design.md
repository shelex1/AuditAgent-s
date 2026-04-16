# AntiHacker Council — Design Spec

**Date:** 2026-04-16
**Status:** Draft (awaiting user review)
**Owner:** alexstayaapp@gmail.com
**Purpose:** MCP server that turns Claude Code into the commander of a 5-model OpenRouter council for code analysis, vulnerability hunting, and bug investigation.

---

## 1. Goals and Non-Goals

### Goals

- Offload expensive analytical work (security audits, bug root-cause analysis, code review) from Claude Code onto free OpenRouter models.
- Provide Claude Code with structured, aggregated verdicts and ready-to-apply unified diff patches — so Claude only spends tokens on reviewing a compact final report, not on the full deliberation.
- Support three interaction modes: targeted (specific files), project-wide scan (large codebases), bug investigation (hypothesis-driven debugging).
- Make the council truly deliberative: 3 debate rounds where models see each other's arguments and can revise positions.
- Never apply changes autonomously: every patch is written to disk as a `.patch` file, and Claude (the commander) decides whether to `git apply` it.

### Non-Goals

- Real-time streaming of debates to the user (debates are opaque; only the final report is surfaced).
- Autonomous execution of patches (explicit user decision: A+D combo — report + patch file on disk, no auto-apply).
- Support for arbitrary AI providers beyond OpenRouter v1 (may be added later, not in scope now).
- A web UI, a Telegram bot, or a CLI front-end (the MCP interface is the only entry point).
- Fine-tuning or training models.
- Caching embeddings / RAG over the project (may be added later, not needed for v1).

---

## 2. High-Level Architecture

```
Claude Code (commander)
       │
       │ MCP protocol over stdio
       ▼
┌─────────────────────────┐
│  AntiHacker MCP Server  │  Python 3.11+, mcp SDK
│                         │
│  Tool Registry          │  consult_council, scan_project,
│                         │  investigate_bug, get_debate_log,
│                         │  list_proposals
│         │               │
│         ▼               │
│  Debate Orchestra       │  3 rounds, asyncio.gather
│         │               │
│         ▼               │
│  Council Members (5)    │  OpenRouter API via openai SDK
│         │               │
│         ▼               │
│  Verdict Aggregator     │  voting + patch selection
└─────────┼───────────────┘
          ▼
   ./debates/*.json         (full deliberation logs)
   ./council_proposals/*.patch (unified diffs, awaiting apply)
```

The commander (Claude) calls an MCP tool → the server runs a multi-round debate among 5 models → the aggregator picks the winning verdict and patch → the commander receives a compact structured report (~500–1500 tokens) and decides what to do with the patch.

---

## 3. MCP Tools

Tools exposed to Claude Code via the MCP server:

| Tool | Purpose | Input | Output |
|---|---|---|---|
| `consult_council` | Targeted analysis of specific files | `task: str`, `files: list[str]`, `mode: "review"\|"security"\|"refactor"\|"free"` | Report JSON with findings, confidence, `patch_file` path |
| `scan_project` | Autonomous project-wide scan | `project_path: str`, `focus: "security"\|"quality"\|"perf"\|"all"`, `max_files: int = 50` | Ranked findings list with per-finding patches |
| `investigate_bug` | Hypothesis-driven bug investigation | `symptom: str`, `related_files: list[str]`, `reproduction: str?`, `stack_trace: str?` | Winning hypothesis + root cause + patch |
| `get_debate_log` | Retrieve full deliberation log | `debate_id: str` | Full JSON log from `./debates/` |
| `list_proposals` | List pending patches | — | List of `.patch` files with metadata |

### Compact report shape returned to Claude

```json
{
  "debate_id": "2026-04-16_14-52-03_a7f3",
  "verdict": "FOUND_VULNERABILITIES" | "NO_ISSUES" | "SPLIT" | "QUORUM_LOST",
  "confidence": "4/5 models agree",
  "findings": [
    {
      "line": 42,
      "severity": "critical|high|medium|low",
      "description": "...",
      "supporting_models": ["model-a", "model-b", ...],
      "dissenting_models": ["model-c"],
      "dissent_reason": "..."
    }
  ],
  "patch_file": "./council_proposals/<debate_id>.patch",
  "alternative_patches": 1,
  "full_log": "./debates/<debate_id>.json",
  "log_persisted": true,
  "status": "success" | "partial_timeout" | "quorum_lost" | "openrouter_unavailable"
}
```

---

## 4. Data Flow — One Debate

For `consult_council(files=["auth.py"], task="find SQL injections", mode="security")`:

1. **T=0 Setup:** read file, generate `debate_id`, initialize `DebateLog` (streaming write to disk), assemble 5 `CouncilMember` instances from config.
2. **T=5s Round 1 (independent):** all 5 models get the file + task simultaneously (`asyncio.gather`). Each returns JSON `{findings, confidence, reasoning}`. Per-member timeout 90s; failed members → `abstained` and debate continues with the rest.
3. **T=35s Round 2 (cross-review):** each model sees the other four's findings. Returns JSON `{agree_with, disagree_with, missed_findings, updated_confidence}`. Models can actually change their minds here.
4. **T=80s Round 3 (final vote + patch):** each model gives final verdict + unified diff patch.
5. **T=120s Aggregator:**
   - A finding is accepted if ≥3/5 models back it in the final round.
   - Patches are grouped by AST/text similarity; the largest group wins; others stored as `alternative_patches`.
   - Winning patch is validated with `git apply --check` before being written to `./council_proposals/<debate_id>.patch`.
   - Full deliberation log written atomically to `./debates/<debate_id>.json`.
6. **T=125s Compact response** (~500–1500 tokens) returned to Claude.

### Variants

- `scan_project`: a **Cartographer** (one fast model, e.g., Gemini Flash, separate from the council to preserve rate limits) builds a project map `{file: summary + risk_score 0–10}` with a hard limit `max_files`. Top-N by risk go through the full `consult_council` cycle. A global `max_additional_file_requests` cap (default 3) protects against runaway reads.
- `investigate_bug`: Round 1 produces hypotheses (not findings). Round 2 models may call `request_file` to pull in related code (subject to the file-request cap). Round 3 votes on root cause + patch.

---

## 5. Components (Python package layout)

```
AntiHacker/
├── pyproject.toml                 # mcp, openai, python-dotenv, pydantic
├── .env.example                   # OPENROUTER_API_KEY=
├── config/
│   └── council.toml               # user-edited: members, timeouts, limits
├── src/anti_hacker/
│   ├── server.py                  # MCP server: tool registration, stdio transport
│   ├── config.py                  # loads council.toml + .env (pydantic)
│   ├── tools/                     # thin wrappers over core
│   │   ├── consult.py             # consult_council
│   │   ├── scan.py                # scan_project
│   │   ├── investigate.py         # investigate_bug
│   │   └── logs.py                # get_debate_log, list_proposals
│   ├── council/                   # core — models and debates
│   │   ├── member.py              # CouncilMember (1 model = 1 instance)
│   │   ├── orchestra.py           # DebateOrchestra: 3 rounds, parallel calls
│   │   ├── prompts.py             # system prompts per round / per role
│   │   └── aggregator.py          # voting, consensus, patch selection
│   ├── scanners/
│   │   ├── cartographer.py        # project map builder
│   │   └── file_filter.py         # .gitignore + excludes, text/binary detection
│   ├── io/
│   │   ├── debate_log.py          # read/write ./debates/*.json (atomic)
│   │   └── proposals.py           # write unified diffs to ./council_proposals/
│   └── openrouter/
│       └── client.py              # async client with retry, rate-limit, fallback
├── debates/                       # runtime (gitignored)
├── council_proposals/             # runtime (gitignored)
├── logs/                          # runtime (gitignored)
└── tests/
    ├── test_aggregator.py
    ├── test_cartographer.py
    ├── test_orchestra.py
    ├── test_prompts.py
    ├── test_patch_io.py
    ├── test_cache.py
    ├── test_e2e.py
    └── test_mcp_protocol.py
```

### Example `council.toml`

```toml
[[members]]
name = "deepseek-r1"
model = "deepseek/deepseek-r1:free"
role = "security-paranoid"
timeout = 90

[[members]]
name = "llama-3.3"
model = "meta-llama/llama-3.3-70b-instruct:free"
role = "pragmatic-engineer"
timeout = 60

# ...3 more members (user-provided list)

[cartographer]
model = "google/gemini-2.0-flash-exp:free"

[limits]
max_files_scan = 50
max_additional_file_requests = 3
debate_timeout = 180
```

### Key design decisions

- **`CouncilMember`** encapsulates one model: ID, (optional custom) API URL, role-based system prompt, per-member timeouts. Adding/removing a member is a config edit, not a code change.
- **`DebateOrchestra`** runs the three rounds. Each round is `asyncio.gather` with per-member try/except — failure of one member never aborts the debate.
- **`Aggregator`** is the single place that turns raw model outputs into a verdict and picks a patch. All consensus logic is centralized here for testability.
- **`Cartographer`** uses a single fast model separate from the council — keeps rate limits for the council itself intact.

---

## 6. Error Handling and Resilience

Free OpenRouter models fail often (rate limits, timeouts, empty responses, malformed JSON). Policy:

1. **HTTP 429 / rate limit:** exponential backoff 2s → 5s → 15s (3 retries). Exhausted → mark member `abstained`, continue with the rest. Quorum requires ≥3 active members; otherwise return `status: "quorum_lost"`.
2. **Per-member timeout (default 90s):** same as above — `abstained` and continue.
3. **Global debate timeout (`debate_timeout = 180s`):** hard cutoff; all outstanding asyncio tasks are cancelled; return `status: "partial_timeout"` with whatever is complete.
4. **Malformed JSON:** single retry-with-repair (send back the bad response and the schema). Still malformed → `abstained`; raw response preserved in the log. No regex hacks to "extract JSON" from prose.
5. **Malformed unified diff:** validated with `git apply --check` before being saved. If the check fails the model's patch is excluded from patch voting, but its findings vote still counts. Report notes `"X/5 models proposed broken patches — excluded"`.
6. **OpenRouter unreachable (network down):** fail fast with `status: "openrouter_unavailable"`. No infinite retry loops.
7. **Cartographer failed in `scan_project`:** return error and suggest falling back to manual `consult_council` with explicit files.
8. **Disk full / unwritable `./debates` or `./council_proposals`:** fail-fast on server startup (checked at init). If write fails mid-debate, the debate still returns a result to Claude but with `"log_persisted": false` — so Claude knows the audit trail is missing.
9. **Patch fails `git apply` at Claude's end:** Claude's problem, not the server's. Claude can `get_debate_log` to see `alternative_patches` or fix the diff manually.
10. **Deduplication:** hash `(task + files_content + mode)` — identical request within 10 minutes returns the cached result. `force_fresh=true` bypasses the cache.

### Server logging

- stderr (MCP standard for logs) + structured JSON in `./logs/server.log`.
- Levels: DEBUG/INFO/WARN/ERROR. Default INFO.
- Logged: debate start/end, retries, timeouts, failures. **Not logged:** prompt bodies (those live in `./debates/`).

### Invariants

- A single model failure never crashes a debate.
- Malformed JSON never crashes the server.
- `debate_timeout` is always honored (hard cutoff).
- All disk writes are atomic (`.tmp` → rename).

---

## 7. Testing Strategy

**Philosophy:** mock the network to OpenRouter, run the rest of the pipeline (JSON parsing, voting, disk IO, patch validation) on real data.

### Unit tests (fast, deterministic)

- **`test_aggregator.py`** — core logic. Fixed sets of 5 mock responses → verify:
  - 3/5 agreement → consensus
  - 2/2/1 split → `split` status
  - Identical patches → single winner
  - Whitespace-different but semantically identical patches → grouped, single winner
  - Divergent patches → majority winner, rest in `alternative_patches`
  - Abstentions → quorum recalculated
  - <3 active members → `quorum_lost`
- **`test_cartographer.py`** — project map parsing, JSON errors, `.gitignore` respected, binaries filtered.
- **`test_prompts.py`** — file content escaping (no prompt injection via comments), size limits with explicit `[TRUNCATED]` marker.
- **`test_patch_io.py`** — unified diff writes, `git apply --check` invoked pre-save, atomic `.tmp` → rename.

### Integration tests (mocked OpenRouter)

- **`test_orchestra.py`** — full 3-round run through `httpx.MockTransport`:
  - Happy path (all 5 respond)
  - 1 timeout → abstained, 4-way debate
  - 2 malformed JSON → repair retry; still bad → abstained
  - Global timeout fires mid-round → `partial_timeout`
  - 429 with `Retry-After` → wait + retry
- **`test_cache.py`** — dedup logic, `force_fresh=true` behavior.

### E2E test

- **`test_e2e.py`** — temp project with a known vulnerability (`os.system(user_input)`), mocked OpenRouter making models "find" it, full `consult_council` run. Verify:
  - Final report contains the finding on the right line
  - `.patch` file created, `git apply` applies cleanly
  - `debate_log.json` contains all 3 rounds
  - Compact report to Claude ≤ 2000 tokens (estimated via length)

### MCP protocol test

- **`test_mcp_protocol.py`** — spawn the MCP server as a subprocess, speak JSON-RPC over stdio. Verify:
  - `tools/list` returns all 5 tools with correct schemas
  - `tools/call consult_council` works end-to-end
  - Invalid tool parameters → correct JSON-RPC error; server stays up

### Smoke test (manual, not CI)

- **`smoke_test.py`** — sends a trivial request to every configured model. Verifies API key, model availability, timeout budgets. Run after edits to `council.toml`.

### Not tested

- Quality of model answers (emergent, inspected via `debate_log`)
- OpenRouter itself (external; fully mocked)
- Claude Code's commander logic (out of scope)

### Tooling

- `pytest` + `pytest-asyncio`
- `httpx.MockTransport` for HTTP mocking
- `pytest-cov` — ≥80% coverage on `council/`, `scanners/`, `aggregator/`

---

## 8. Security Considerations (the server itself)

- `OPENROUTER_API_KEY` lives only in `.env` (gitignored); never logged, never written to `./debates/`.
- File content passed to models is never sent to any endpoint other than OpenRouter.
- File paths passed to `consult_council` and `scan_project` must resolve under the project root (prevent `../../etc/passwd` reads). Reject absolute paths outside the configured project root.
- `scan_project` respects `.gitignore` and an explicit exclude list (e.g., `node_modules`, `.venv`, `dist`).
- Binary files are skipped (detection: null byte in first 8KB).
- Patches are validated with `git apply --check` before being written — a broken patch never pollutes `./council_proposals/`.
- The server never executes arbitrary code — it only reads files and writes logs/patches.

---

## 9. Open Questions for User Review

- User will provide the exact list of 5 models and any custom API endpoints for `council.toml`.
- Default `max_files_scan = 50` — acceptable or should it be higher/lower?
- Default `debate_timeout = 180s` — acceptable, or need a longer budget for deep investigations?
- Cache TTL of 10 minutes for deduplication — acceptable?

---

## 10. Out of Scope for v1 (possible v2+)

- Streaming debate events to the commander in real time
- Web UI for inspecting `./debates/` logs
- Embeddings / RAG over the project for semantic retrieval
- Auto-apply mode (explicitly rejected for v1 — safety)
- Support for non-OpenRouter providers
- Dynamic council (fallback models if a primary is down)
