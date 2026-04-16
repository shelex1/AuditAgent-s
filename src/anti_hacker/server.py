from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

from .config import load_config
from .council.cache import DebateCache
from .openrouter.client import OpenRouterClient
from .tools.consult import ConsultService


logger = logging.getLogger("anti_hacker")


def _make_service(project_root: Path, data_root: Path) -> ConsultService:
    config_path = Path(os.getenv("ANTI_HACKER_CONFIG", project_root / "config" / "council.toml"))
    cfg = load_config(config_path)
    client = OpenRouterClient(api_key=cfg.api_key, base_url=cfg.openrouter.base_url)
    cache = DebateCache(ttl_seconds=cfg.limits.cache_ttl_seconds)
    return ConsultService(
        config=cfg,
        client=client,
        cache=cache,
        project_root=project_root,
        data_root=data_root,
    )


def build_server(project_root: Path, data_root: Path) -> Server:
    server = Server("anti-hacker")
    service = _make_service(project_root, data_root)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="consult_council",
                description="Run a 3-round debate among 5 OpenRouter models on the given files.",
                inputSchema={
                    "type": "object",
                    "required": ["task", "files"],
                    "properties": {
                        "task": {"type": "string"},
                        "files": {"type": "array", "items": {"type": "string"}},
                        "mode": {"type": "string", "enum": ["review", "security", "refactor", "free"], "default": "review"},
                        "force_fresh": {"type": "boolean", "default": False},
                    },
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        if name == "consult_council":
            report = await service.consult(
                task=arguments["task"],
                files=arguments["files"],
                mode=arguments.get("mode", "review"),
                force_fresh=arguments.get("force_fresh", False),
            )
            import json
            return [types.TextContent(type="text", text=json.dumps(report, ensure_ascii=False, indent=2))]
        raise ValueError(f"unknown tool: {name}")

    return server


def main() -> None:
    logging.basicConfig(
        level=os.getenv("ANTI_HACKER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    project_root = Path(os.getenv("ANTI_HACKER_PROJECT_ROOT", Path.cwd())).resolve()
    data_root = Path(os.getenv("ANTI_HACKER_DATA_ROOT", project_root)).resolve()
    server = build_server(project_root=project_root, data_root=data_root)

    async def _run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
