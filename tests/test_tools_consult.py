import json
from pathlib import Path

import httpx
import pytest

from anti_hacker.config import Config, MemberConfig, CartographerConfig, LimitsConfig, OpenRouterConfig
from anti_hacker.council.cache import DebateCache
from anti_hacker.openrouter.client import OpenRouterClient
from anti_hacker.tools.consult import ConsultService
from tests.fixtures.sample_responses import chat_completion


R1 = json.dumps({"findings": [{"line": 1, "severity": "high", "description": "bad", "proposed_fix": "fix"}], "confidence": 8, "reasoning": "r"})
R2 = json.dumps({"agree_with": ["bad"], "disagree_with": [], "missed_findings": [], "updated_confidence": 8})
R3 = json.dumps({
    "final_findings": [{"line": 1, "severity": "high", "description": "bad"}],
    "unified_patch": "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-print(1)\n+print(2)\n",
    "final_confidence": 8,
})


def _config() -> Config:
    return Config(
        api_key="sk-test",
        members=[
            MemberConfig(name=f"m{i}", model=f"p/m{i}:free", role="pragmatic-engineer", timeout=5)
            for i in range(1, 6)
        ],
        cartographer=CartographerConfig(model="p/fast:free", timeout=60),
        limits=LimitsConfig(),
        openrouter=OpenRouterConfig(),
    )


def _transport_sequence(responses: list[str]) -> httpx.MockTransport:
    counter = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = counter["i"]
        counter["i"] += 1
        return httpx.Response(200, json=chat_completion(responses[min(i, len(responses) - 1)]))

    return httpx.MockTransport(handler)


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "x.py").write_text("print(1)\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.mark.asyncio
async def test_consult_happy_path(git_project: Path) -> None:
    cfg = _config()
    # every member returns R1, R2, R3 in order (shared transport per client is fine for this canned sequence)
    transport = _transport_sequence([R1, R2, R3])
    client = OpenRouterClient(api_key=cfg.api_key, base_url=cfg.openrouter.base_url, transport=transport, retry_backoff=lambda a: 0)
    cache = DebateCache(ttl_seconds=0)
    service = ConsultService(config=cfg, client=client, cache=cache, project_root=git_project, data_root=git_project)

    report = await service.consult(task="find bugs", files=["x.py"], mode="review")

    assert report["verdict"] == "FOUND"
    assert report["status"] == "success"
    assert Path(report["patch_file"]).exists()
    assert Path(report["full_log"]).exists()


@pytest.mark.asyncio
async def test_consult_rejects_path_outside_root(tmp_path: Path) -> None:
    cfg = _config()
    transport = _transport_sequence([R1, R2, R3])
    client = OpenRouterClient(api_key=cfg.api_key, base_url=cfg.openrouter.base_url, transport=transport, retry_backoff=lambda a: 0)
    cache = DebateCache(ttl_seconds=0)
    outside = tmp_path.parent / "outside.py"
    outside.write_text("x", encoding="utf-8")
    try:
        service = ConsultService(config=cfg, client=client, cache=cache, project_root=tmp_path, data_root=tmp_path)
        with pytest.raises(ValueError, match="outside project root"):
            await service.consult(task="t", files=[str(outside)], mode="review")
    finally:
        outside.unlink()
