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


def _make_services(project_root: Path, data_root: Path) -> tuple[ConsultService, "ScanService", "InvestigateService", "LogService", "ThinkingService"]:
    from .scanners.cartographer import Cartographer
    from .tools.scan import ScanService
    from .tools.investigate import InvestigateService
    from .tools.logs import LogService
    from .tools.thinking import ThinkingService
    config_path = Path(os.getenv("ANTI_HACKER_CONFIG", project_root / "config" / "council.toml"))
    cfg = load_config(config_path)
    clients: dict[str, OpenRouterClient] = {
        p.name: OpenRouterClient(
            api_key=p.api_key,
            base_url=p.base_url,
            empty_means_quota=p.empty_means_quota,
        )
        for p in cfg.providers
    }
    cache = DebateCache(ttl_seconds=cfg.limits.cache_ttl_seconds)
    consult = ConsultService(config=cfg, clients=clients, cache=cache, project_root=project_root, data_root=data_root)
    cart_client = clients[cfg.cartographer.provider]
    cart = Cartographer(client=cart_client, model=cfg.cartographer.model, timeout=cfg.cartographer.timeout)
    scan = ScanService(cartographer=cart, consult=consult, project_root=project_root)
    investigate = InvestigateService(consult=consult)
    logs = LogService(data_root=data_root)
    thinking = ThinkingService()
    return consult, scan, investigate, logs, thinking


def build_server(project_root: Path, data_root: Path) -> Server:
    server = Server("anti-hacker")
    consult, scan, investigate, logs, thinking = _make_services(project_root=project_root, data_root=data_root)

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
            types.Tool(
                name="sequential_thinking",
                description=(
                    "A detailed tool for dynamic and reflective problem-solving through thoughts. "
                    "This tool helps analyze problems through a flexible thinking process that can "
                    "adapt and evolve. Each thought can build on, question, or revise previous "
                    "insights as understanding deepens.\n\n"
                    "When to use this tool:\n"
                    "- Breaking down complex problems into steps\n"
                    "- Planning and design with room for revision\n"
                    "- Analysis that might need course correction\n"
                    "- Problems where the full scope might not be clear initially\n"
                    "- Problems that require a multi-step solution\n"
                    "- Tasks that need to maintain context over multiple steps\n"
                    "- Situations where irrelevant information needs to be filtered out\n\n"
                    "Key features:\n"
                    "- You can adjust total_thoughts up or down as you progress\n"
                    "- You can question or revise previous thoughts\n"
                    "- You can add more thoughts even after reaching what seemed like the end\n"
                    "- You can express uncertainty and explore alternative approaches\n"
                    "- Not every thought needs to build linearly — you can branch or backtrack\n"
                    "- Generates a solution hypothesis\n"
                    "- Verifies the hypothesis based on the Chain of Thought steps\n"
                    "- Repeats the process until satisfied\n"
                    "- Provides a correct answer\n\n"
                    "Parameters explained:\n"
                    "- thought: Your current thinking step (analytical step, revision, question, "
                    "realization, change of approach, hypothesis generation, or hypothesis verification)\n"
                    "- next_thought_needed: True if you need more thinking, even if at what seemed like the end\n"
                    "- thought_number: Current number in sequence (can go beyond initial total_thoughts if needed)\n"
                    "- total_thoughts: Current estimate of thoughts needed (can be adjusted up/down)\n"
                    "- is_revision: Whether this thought revises previous thinking\n"
                    "- revises_thought: If is_revision is true, which thought number is being reconsidered\n"
                    "- branch_from_thought: If branching, which thought number is the branching point\n"
                    "- branch_id: Identifier for the current branch (if any)\n"
                    "- needs_more_thoughts: If reaching end but realizing more thoughts needed\n\n"
                    "You should:\n"
                    "1. Start with an initial estimate of needed thoughts, but be ready to adjust\n"
                    "2. Feel free to question or revise previous thoughts\n"
                    "3. Don't hesitate to add more thoughts if needed, even at the \"end\"\n"
                    "4. Express uncertainty when present\n"
                    "5. Mark thoughts that revise previous thinking or branch into new paths\n"
                    "6. Ignore information that is irrelevant to the current step\n"
                    "7. Generate a solution hypothesis when appropriate\n"
                    "8. Verify the hypothesis based on the Chain of Thought steps\n"
                    "9. Repeat the process until satisfied with the solution\n"
                    "10. Provide a single, ideally correct answer as the final output\n"
                    "11. Only set next_thought_needed to false when truly done and a satisfactory answer is reached"
                ),
                inputSchema={
                    "type": "object",
                    "required": ["thought", "thought_number", "total_thoughts", "next_thought_needed"],
                    "properties": {
                        "thought": {"type": "string", "description": "Your current thinking step"},
                        "thought_number": {"type": "integer", "minimum": 1, "description": "Current thought number"},
                        "total_thoughts": {"type": "integer", "minimum": 1, "description": "Estimated total thoughts needed"},
                        "next_thought_needed": {"type": "boolean", "description": "Whether another thought step is needed"},
                        "is_revision": {"type": "boolean", "description": "Whether this revises previous thinking"},
                        "revises_thought": {"type": "integer", "minimum": 1, "description": "Which thought is being reconsidered"},
                        "branch_from_thought": {"type": "integer", "minimum": 1, "description": "Branching point thought number"},
                        "branch_id": {"type": "string", "description": "Branch identifier"},
                        "needs_more_thoughts": {"type": "boolean", "description": "If more thoughts are needed"},
                    },
                },
            ),
            types.Tool(
                name="get_thought_history",
                description=(
                    "Return the full sequential-thinking history accumulated in this server process. "
                    "If branch_id is provided, returns only the thoughts belonging to that branch."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "branch_id": {"type": "string", "description": "Optional branch filter"},
                    },
                },
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
        if name == "sequential_thinking":
            import json
            result = thinking.process_thought(**arguments)
            return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
        if name == "get_thought_history":
            import json
            result = thinking.get_history(arguments.get("branch_id"))
            return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

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
