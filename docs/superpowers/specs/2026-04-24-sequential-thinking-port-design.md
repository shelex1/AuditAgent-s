# Sequential Thinking Port — Design

**Date:** 2026-04-24
**Status:** Approved
**Scope:** Port the `@modelcontextprotocol/server-sequential-thinking` tool (TypeScript) into the AntiHacker Python MCP server as two new tools: `sequential_thinking` and `get_thought_history`.

## Motivation

AntiHacker currently exposes five MCP tools built around the 5-model council (`consult_council`, `scan_project`, `investigate_bug`, `get_debate_log`, `list_proposals`). Users of the same MCP host (Claude Code, Claude Desktop, VS Code, Codex) frequently also want the `sequential_thinking` tool from `@modelcontextprotocol/server-sequential-thinking` — a stateful scratchpad for stepwise reasoning with revisions and branches.

Instead of running two separate MCP servers, we port the upstream logic (~100 lines of TypeScript) into AntiHacker so a single stdio process exposes seven tools and there is one deploy, one config, one version to update.

## Non-Goals

- Persistence to disk. History lives in memory for the lifetime of the process, matching upstream semantics.
- A `reset_thinking` tool. Upstream does not have one; the scope does not need it.
- Pydantic models for `ThoughtData`. Existing services use plain `dict[str, Any]` as their wire format; introducing a new style for one service is not justified.
- Integration with `DebateCache`, `OpenRouterClient`, or any council machinery. The new tool is a pure in-process state manager; it does no network I/O.
- Preserving the upstream's ASCII-box stderr output or the `DISABLE_THOUGHT_LOGGING` env var. We use the project's standard `logging` module and existing `ANTI_HACKER_LOG_LEVEL` control.

## Architecture

### New files

- `src/anti_hacker/tools/thinking.py` — the `ThinkingService` class and module-level type aliases. Target: ~80 lines.
- `tests/test_tools_thinking.py` — unit tests for the service.

### Modified files

- `src/anti_hacker/server.py`:
  - `_make_services()` constructs a `ThinkingService` instance and returns it alongside the existing services.
  - `list_tools()` emits two additional `types.Tool` entries.
  - `call_tool()` dispatches two additional tool names.
- `tests/test_mcp_protocol.py`:
  - Existing assertion on the tool list is extended to include the two new names.

## Components

### `ThinkingService` (src/anti_hacker/tools/thinking.py)

```python
class ThinkingService:
    def __init__(self) -> None:
        self._history: list[dict[str, Any]] = []
        self._branches: dict[str, list[dict[str, Any]]] = {}
        self._logger = logging.getLogger("anti_hacker.thinking")

    def process_thought(
        self,
        *,
        thought: str,
        thought_number: int,
        total_thoughts: int,
        next_thought_needed: bool,
        is_revision: bool | None = None,
        revises_thought: int | None = None,
        branch_from_thought: int | None = None,
        branch_id: str | None = None,
        needs_more_thoughts: bool | None = None,
    ) -> dict[str, Any]: ...

    def get_history(self, branch_id: str | None = None) -> dict[str, Any]: ...
```

- State lives in the instance. One instance is constructed in `_make_services()` and lives for the lifetime of the MCP server process — identical lifecycle to upstream.
- No thread-safety guards: MCP handlers run on a single asyncio event loop; there are no parallel mutations.
- Input validation is performed entirely in the JSON schemas registered in `list_tools()` (same convention as the other AntiHacker tools). The service trusts its caller.
- `process_thought()` behavior:
  1. If `thought_number > total_thoughts`, set `total_thoughts = thought_number` (auto-bump, matches upstream).
  2. Append a snapshot dict of all input fields to `self._history`.
  3. If `branch_from_thought` **and** `branch_id` are both provided, append the same snapshot to `self._branches[branch_id]` (create the list if it does not exist).
  4. Log one line at `INFO` via `self._logger` (see Logging).
  5. Return the summary dict (see Output schemas).
- `get_history()` behavior:
  - No argument → return `{"history": [...], "branches": {...}, "total": len(history)}`.
  - `branch_id` set → return `{"history": self._branches.get(branch_id, []), "total": ...}` with no `branches` key.

### Input schemas

**`sequential_thinking`:**

| Field | Type | Required | Notes |
|---|---|---|---|
| `thought` | string | yes | The current reasoning step. |
| `thought_number` | integer ≥ 1 | yes | Current position. May exceed `total_thoughts`. |
| `total_thoughts` | integer ≥ 1 | yes | Current estimate; auto-bumped if exceeded. |
| `next_thought_needed` | boolean | yes | `false` signals the reasoning chain is done. |
| `is_revision` | boolean | no | Marks the thought as revising an earlier one. |
| `revises_thought` | integer ≥ 1 | no | Which earlier thought is being revised. |
| `branch_from_thought` | integer ≥ 1 | no | Branching point index. |
| `branch_id` | string | no | Branch identifier. Required together with `branch_from_thought` to register the branch. |
| `needs_more_thoughts` | boolean | no | "Thought we were done, but we need more". |

**`get_thought_history`:**

| Field | Type | Required | Notes |
|---|---|---|---|
| `branch_id` | string | no | If set, return only this branch. |

### Output shapes

**`sequential_thinking`** returns text content containing JSON:

```json
{
  "thought_number": 3,
  "total_thoughts": 5,
  "next_thought_needed": true,
  "branches": ["branch-a"],
  "thought_history_length": 4
}
```

Field semantics match upstream 1:1 but use snake_case throughout.

**`get_thought_history`** (no `branch_id`):

```json
{
  "history": [ {"thought": "...", "thought_number": 1, ...}, ... ],
  "branches": {"branch-a": [ ... ]},
  "total": 4
}
```

**`get_thought_history`** (with `branch_id`):

```json
{
  "history": [ ... only the branch ... ],
  "total": 2
}
```

### Tool description text

The `sequential_thinking` tool description preserves the upstream long-form guidance that instructs the model how to use the tool (break problems down, revise, branch, generate and verify hypotheses). Without it the tool has little value — the description is where the behavior lives. Minor adaptation: field names updated to snake_case, and the mention of `is_revision` / `revisesThought` updated accordingly.

`get_thought_history` gets a short description (one or two sentences): "Return the full sequential-thinking history accumulated in this server process, optionally filtered by `branch_id`. Useful for replaying a reasoning chain after the process has been active for a while."

## Logging

- Single logger: `logging.getLogger("anti_hacker.thinking")`.
- Server-wide format (`"%(asctime)s %(levelname)s %(name)s - %(message)s"`) is inherited — no custom handler, no color codes, no ASCII boxes.
- One line per thought at `INFO`. Format priority (matches upstream): revision → branch → plain.
  - Regular: `💭 thought N/M "..."`
  - Revision (when `is_revision=True`): `🔄 revise N→K "..."` where `K` is `revises_thought`.
  - Branch (when `branch_from_thought` set and not a revision): `🌿 <branch_id> N/M "..."`.
  - Thought text is truncated to 120 characters with a trailing `…` if longer.
- Users who want the tool silent: set `ANTI_HACKER_LOG_LEVEL=WARNING` (already wired in `server.main()`). No new env var.

## Wiring into server.py

- `_make_services()` signature changes to return `tuple[ConsultService, ScanService, InvestigateService, LogService, ThinkingService]`.
- `build_server()` unpacks the extra service.
- `list_tools()` appends two `types.Tool` entries with the schemas above.
- `call_tool()` appends two branches:
  ```python
  if name == "sequential_thinking":
      result = thinking.process_thought(**arguments)
      return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
  if name == "get_thought_history":
      result = thinking.get_history(arguments.get("branch_id"))
      return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
  ```

## Testing

### `tests/test_tools_thinking.py`

Plain pytest functions, same style as `test_tools_logs.py`.

1. `test_basic_thought_appends_to_history` — one call, `thought_history_length == 1`, no error.
2. `test_optional_fields_accepted` — call with `is_revision=True, revises_thought=1, needs_more_thoughts=False` → no error.
3. `test_three_thoughts_grow_history` — three sequential calls → history length 3, final `next_thought_needed=False`.
4. `test_auto_bump_total_thoughts` — `thought_number=5, total_thoughts=3` → response has `total_thoughts=5`.
5. `test_two_branches_tracked_independently` — two calls with different `branch_id`s → `branches=["a","b"]` (order-agnostic).
6. `test_multiple_thoughts_in_same_branch` — two calls with the same `branch_id` → `branches` still length 1, branch list length 2.
7. `test_get_history_full_snapshot` — after 3 calls + 1 branch call, `get_history()` returns `history` of length 4 and `branches` dict with one entry.
8. `test_get_history_filtered_by_branch` — with `branch_id` set, returns only that branch's thoughts and no `branches` key.
9. `test_long_thought_accepted` — `thought` of 10_000 characters → no error.

### `tests/test_mcp_protocol.py`

Extend the existing tool-listing assertion to include `"sequential_thinking"` and `"get_thought_history"` in the set of expected tool names.

## Risk / Rollback

- The new tools are additive; they do not touch any existing code path. Rollback is a revert of the one wiring commit.
- No new dependencies introduced (no `chalk` analogue; logging stdlib only).
- No new env vars.
- No config file changes.

## Out-of-scope follow-ups (not part of this plan)

- Persisting thought history to `data_root` in the same spirit as `DebateLog` (would mirror `get_debate_log`).
- A `reset_thinking` tool.
- Exposing the sequentialthinking TypeScript sibling directory — leave it untouched for reference.
