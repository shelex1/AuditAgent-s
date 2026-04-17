import asyncio
import json
import os
import sys
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_server_lists_all_tools(tmp_path: Path, monkeypatch) -> None:
    # minimal council.toml
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "council.toml").write_text(
        """
[[members]]
name="m1"
model="p/m1:free"
role="pragmatic-engineer"
timeout=30

[[members]]
name="m2"
model="p/m2:free"
role="pragmatic-engineer"
timeout=30

[[members]]
name="m3"
model="p/m3:free"
role="pragmatic-engineer"
timeout=30

[[members]]
name="m4"
model="p/m4:free"
role="pragmatic-engineer"
timeout=30

[[members]]
name="m5"
model="p/m5:free"
role="pragmatic-engineer"
timeout=30

[cartographer]
model="p/fast:free"
timeout=60

[limits]
max_files_scan=50
max_additional_file_requests=3
debate_timeout=180
per_member_timeout_fallback=90
cache_ttl_seconds=600
max_file_size_bytes=51200

[openrouter]
base_url="https://openrouter.ai/api/v1"
""",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-test\n", encoding="utf-8")

    env = os.environ.copy()
    env["ANTI_HACKER_PROJECT_ROOT"] = str(tmp_path)
    env["ANTI_HACKER_CONFIG"] = str(cfg_dir / "council.toml")
    env["OPENROUTER_API_KEY"] = "sk-test"

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "anti_hacker.server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=tmp_path,
    )

    async def send(msg: dict) -> None:
        proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        await proc.stdin.drain()

    async def recv() -> dict:
        line = await proc.stdout.readline()
        return json.loads(line.decode("utf-8"))

    async def recv_timeout() -> dict:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=10.0)
        return json.loads(line.decode("utf-8"))

    try:
        # initialize
        await send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        }})
        await recv_timeout()

        # send initialized notification (required by MCP protocol before any requests)
        await send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # list tools
        await send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resp = await recv_timeout()
        names = {t["name"] for t in resp["result"]["tools"]}
        assert names == {"consult_council", "scan_project", "investigate_bug", "get_debate_log", "list_proposals"}
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except asyncio.TimeoutError:
            proc.kill()
