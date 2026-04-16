import json
from pathlib import Path

import httpx
import pytest

from anti_hacker.openrouter.client import OpenRouterClient
from anti_hacker.scanners.cartographer import Cartographer, FileRisk
from tests.fixtures.sample_responses import chat_completion


def _client(responses: list[str]) -> OpenRouterClient:
    counter = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = counter["i"]
        counter["i"] += 1
        text = responses[min(i, len(responses) - 1)]
        return httpx.Response(200, json=chat_completion(text))

    return OpenRouterClient(
        api_key="sk-test",
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(handler),
        retry_backoff=lambda a: 0,
    )


@pytest.mark.asyncio
async def test_cartographer_ranks_files(tmp_path: Path) -> None:
    (tmp_path / "auth.py").write_text('import os\nos.system(user)\n', encoding="utf-8")
    (tmp_path / "utils.py").write_text("def add(a,b): return a+b\n", encoding="utf-8")
    resp = json.dumps({
        "files": [
            {"file": "auth.py", "risk_score": 9, "summary": "shell injection"},
            {"file": "utils.py", "risk_score": 1, "summary": "pure arithmetic"},
        ]
    })
    c = Cartographer(client=_client([resp]), model="p/fast:free", timeout=10)
    ranked = await c.build_map(tmp_path, max_files=50, focus="security")
    assert ranked[0].path.name == "auth.py"
    assert ranked[0].risk_score == 9
    assert ranked[1].path.name == "utils.py"


@pytest.mark.asyncio
async def test_cartographer_enforces_max_files(tmp_path: Path) -> None:
    for i in range(10):
        (tmp_path / f"f{i}.py").write_text("pass\n", encoding="utf-8")
    resp = json.dumps({"files": [{"file": f"f{i}.py", "risk_score": 1, "summary": "x"} for i in range(10)]})
    c = Cartographer(client=_client([resp]), model="p/fast:free", timeout=10)
    ranked = await c.build_map(tmp_path, max_files=3, focus="security")
    assert len(ranked) == 3


@pytest.mark.asyncio
async def test_cartographer_malformed_json_raises(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("pass\n", encoding="utf-8")
    c = Cartographer(client=_client(["not json"]), model="p/fast:free", timeout=10)
    with pytest.raises(Exception):
        await c.build_map(tmp_path, max_files=10, focus="security")
