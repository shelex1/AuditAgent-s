# AntiHacker Council

Python MCP server that coordinates 5 free OpenRouter models through 3 debate rounds and returns compact verdicts plus unified-diff patches for Claude Code to review.

> **Status:** in-progress implementation. The `anti-hacker` command (step 5 below) becomes functional after the full server is wired up per the plan; until then, `pip install -e .[dev]` and `pytest` work, but launching the MCP server is not yet supported.

## Setup

1. Install: `pip install -e .[dev]`
2. Copy `.env.example` to `.env` and set `OPENROUTER_API_KEY` (primary provider) and `MODAL_API_KEY` (fallback provider — obtain from [Modal Research dashboard](https://modal.com))
3. Copy `config/council.example.toml` to `config/council.toml` and fill in your 5-model council
4. Run tests: `pytest`
5. Register with Claude Code by adding to your MCP config:
   ```json
   {
     "anti-hacker": {
       "command": "anti-hacker",
       "args": []
     }
   }
   ```

See `docs/superpowers/specs/2026-04-16-anti-hacker-council-design.md` for design details.

## First Run

After install, before first MCP use:

1. Fill in `config/council.toml` with your 5 model IDs and roles.
2. Set `OPENROUTER_API_KEY` (primary) and `MODAL_API_KEY` (fallback — from Modal Research dashboard) in `.env`.
3. Run the smoke test to verify every model responds:
   ```
   python scripts/smoke_test.py
   ```
4. Run the full test suite:
   ```
   pytest
   ```
5. Register in Claude Code MCP config and restart the CLI.
