# AntiHacker Council

Python MCP server that coordinates 5 free OpenRouter models through 3 debate rounds and returns compact verdicts plus unified-diff patches for Claude Code to review.

> **Status:** in-progress implementation. The `anti-hacker` command (step 5 below) becomes functional after the full server is wired up per the plan; until then, `pip install -e .[dev]` and `pytest` work, but launching the MCP server is not yet supported.

## Setup

1. Install: `pip install -e .[dev]`
2. Copy `.env.example` to `.env` and set `OPENROUTER_API_KEY`
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
