import json
from pathlib import Path

import httpx
import pytest

from anti_hacker.config import Config, MemberConfig, CartographerConfig, LimitsConfig, ProviderConfig
from anti_hacker.council.cache import DebateCache
from anti_hacker.openrouter.client import OpenRouterClient
from anti_hacker.tools.consult import ConsultService
from anti_hacker.tools.investigate import InvestigateService
from tests.fixtures.sample_responses import chat_completion


R1 = json.dumps({"findings": [{"line": 1, "severity": "high", "description": "null deref", "proposed_fix": "guard"}], "confidence": 8, "reasoning": "r"})
R2 = json.dumps({"agree_with": ["null deref"], "disagree_with": [], "missed_findings": [], "updated_confidence": 8})
R3 = json.dumps({
    "final_findings": [{"line": 1, "severity": "high", "description": "null deref"}],
    "unified_patch": "--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,1 @@\n-x.y\n+x.y if x else None\n",
    "final_confidence": 8,
})


def _config() -> Config:
    return Config(
        providers=[ProviderConfig(name="openrouter", base_url="https://openrouter.ai/api/v1", api_key_env="OPENROUTER_API_KEY", api_key="sk-test")],
        members=[MemberConfig(name=f"m{i}", model=f"p/m{i}:free", role="pragmatic-engineer", timeout=5, provider="openrouter") for i in range(1, 6)],
        cartographer=CartographerConfig(model="p/fast:free", timeout=60),
        limits=LimitsConfig(),
    )


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.py").write_text("x.y\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.mark.asyncio
async def test_investigate_returns_root_cause_and_patch(git_project: Path) -> None:
    cfg = _config()
    sequence = [R1] * 5 + [R2] * 5 + [R3] * 5
    counter = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = counter["i"]
        counter["i"] += 1
        return httpx.Response(200, json=chat_completion(sequence[min(i, len(sequence) - 1)]))

    client = OpenRouterClient(api_key=cfg.providers[0].api_key, base_url=cfg.providers[0].base_url, transport=httpx.MockTransport(handler), retry_backoff=lambda a: 0)
    cache = DebateCache(ttl_seconds=0)
    consult = ConsultService(config=cfg, clients={"openrouter": client}, cache=cache, project_root=git_project, data_root=git_project)
    inv = InvestigateService(consult=consult)

    report = await inv.investigate(
        symptom="AttributeError on x.y",
        related_files=["f.py"],
        reproduction="call f()",
        stack_trace="Traceback: ...",
    )
    assert report["verdict"] == "FOUND"
    assert report["patch_file"]
