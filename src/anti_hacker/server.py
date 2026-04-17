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


def _make_services(project_root: Path, data_root: Path) -> tuple[ConsultService, "ScanService", "InvestigateService", "LogService"]:
    from .scanners.cartographer import Cartographer
    from .tools.scan import ScanService
    from .tools.investigate import InvestigateService
    from .tools.logs import LogService
    config_path = Path(os.getenv("ANTI_HACKER_CONFIG", project_root / "config" / "council.toml"))
    cfg = load_config(config_path)
    clients: dict[str, OpenRouterClient] = {
        p.name: OpenRouterClient(api_key=p.api_key, base_url=p.base_url)
        for p in cfg.providers
    }
    cache = DebateCache(ttl_seconds=cfg.limits.cache_ttl_seconds)
    consult = ConsultService(config=cfg, clients=clients, cache=cache, project_root=project_root, data_root=data_root)
    cart_client = clients[cfg.cartographer.provider]
    cart = Cartographer(client=cart_client, model=cfg.cartographer.model, timeout=cfg.cartographer.timeout)
    scan = ScanService(cartographer=cart, consult=consult, project_root=project_root)
    investigate = InvestigateService(consult=consult)
    logs = LogService(data_root=data_root)
    return consult, scan, investigate, logs


def build_server(project_root: Path, data_root: Path) -> Server:
    server = Server("anti-hacker")
    consult, scan, investigate, logs = _make_services(project_root=project_root, data_root=data_root)

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
            types.Tool(
                name="scan_project",
                description="Build a cartographer map of the project, then deep-review the top-N riskiest files.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "focus": {"type": "string", "enum": ["security", "quality", "perf", "all"], "default": "security"},
                        "max_files": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
                    },
                },
            ),
            types.Tool(
                name="investigate_bug",
                description="Run a hypothesis-driven 3-round debate to pinpoint a bug's root cause.",
                inputSchema={
                    "type": "object",
                    "required": ["symptom", "related_files"],
                    "properties": {
                        "symptom": {"type": "string"},
                        "related_files": {"type": "array", "items": {"type": "string"}},
                        "reproduction": {"type": "string"},
                        "stack_trace": {"type": "string"},
                    },
                },
            ),
            types.Tool(
                name="get_debate_log",
                description="Return the full JSON log for a given debate_id.",
                inputSchema={"type": "object", "required": ["debate_id"], "properties": {"debate_id": {"type": "string"}}},
            ),
            types.Tool(
                name="list_proposals",
                description="List all .patch files awaiting manual apply with their metadata.",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        if name == "consult_council":
            report = await consult.consult(
                task=arguments["task"],
                files=arguments["files"],
                mode=arguments.get("mode", "review"),
                force_fresh=arguments.get("force_fresh", False),
            )
            import json
            return [types.TextContent(type="text", text=json.dumps(report, ensure_ascii=False, indent=2))]
        if name == "scan_project":
            report = await scan.scan(
                focus=arguments.get("focus", "security"),
                max_files=arguments.get("max_files", 50),
            )
            import json
            return [types.TextContent(type="text", text=json.dumps(report, ensure_ascii=False, indent=2))]
        if name == "investigate_bug":
            report = await investigate.investigate(
                symptom=arguments["symptom"],
                related_files=arguments["related_files"],
                reproduction=arguments.get("reproduction"),
                stack_trace=arguments.get("stack_trace"),
            )
            import json
            return [types.TextContent(type="text", text=json.dumps(report, ensure_ascii=False, indent=2))]
        if name == "get_debate_log":
            import json
            payload = logs.get_debate_log(arguments["debate_id"])
            return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]

        if name == "list_proposals":
            import json
            payload = logs.list_proposals()
            return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]

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
