import json
from pathlib import Path

import httpx
import pytest

from anti_hacker.config import Config, MemberConfig, CartographerConfig, LimitsConfig, ProviderConfig
from anti_hacker.council.cache import DebateCache
from anti_hacker.openrouter.client import OpenRouterClient
from anti_hacker.tools.consult import ConsultService
from anti_hacker.tools.scan import ScanService
from anti_hacker.scanners.cartographer import Cartographer
from tests.fixtures.sample_responses import chat_completion


CART_RESP = json.dumps({
    "files": [
        {"file": "bad.py", "risk_score": 9, "summary": "shell injection"},
        {"file": "ok.py", "risk_score": 1, "summary": "fine"},
    ]
})
R1 = json.dumps({"findings": [{"line": 1, "severity": "high", "description": "bad", "proposed_fix": "fix"}], "confidence": 8, "reasoning": "r"})
R2 = json.dumps({"agree_with": ["bad"], "disagree_with": [], "missed_findings": [], "updated_confidence": 8})
R3 = json.dumps({
    "final_findings": [{"line": 1, "severity": "high", "description": "bad"}],
    "unified_patch": "--- a/bad.py\n+++ b/bad.py\n@@ -1,1 +1,1 @@\n-os.system(x)\n+subprocess.run([x])\n",
    "final_confidence": 8,
})


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "bad.py").write_text("os.system(x)\n", encoding="utf-8")
    (tmp_path / "ok.py").write_text("def a(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _config() -> Config:
    return Config(
        providers=[ProviderConfig(name="openrouter", base_url="https://openrouter.ai/api/v1", api_key_env="OPENROUTER_API_KEY", api_key="sk-test")],
        members=[MemberConfig(name=f"m{i}", model=f"p/m{i}:free", role="pragmatic-engineer", timeout=5, provider="openrouter") for i in range(1, 6)],
        cartographer=CartographerConfig(model="p/fast:free", timeout=60),
        limits=LimitsConfig(max_files_scan=5),
    )


def _transport(sequence: list[str]) -> httpx.MockTransport:
    counter = {"i": 0}
    def handler(request: httpx.Request) -> httpx.Response:
        i = counter["i"]
        counter["i"] += 1
        return httpx.Response(200, json=chat_completion(sequence[min(i, len(sequence) - 1)]))
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_scan_project_ranks_and_deep_reviews(git_project: Path) -> None:
    cfg = _config()
    # cartographer call = 1 request; each member round = 5 requests; 3 rounds = 15; so:
    # 1 (cart) + 15 (5 members × 3 rounds) for the top file only (top-1 for test simplicity)
    sequence = [CART_RESP] + [R1] * 5 + [R2] * 5 + [R3] * 5
    transport = _transport(sequence)
    client = OpenRouterClient(api_key=cfg.providers[0].api_key, base_url=cfg.providers[0].base_url, transport=transport, retry_backoff=lambda a: 0)
    cache = DebateCache(ttl_seconds=0)
    cartographer = Cartographer(client=client, model=cfg.cartographer.model, timeout=cfg.cartographer.timeout)
    consult = ConsultService(config=cfg, clients={"openrouter": client}, cache=cache, project_root=git_project, data_root=git_project)
    scan = ScanService(cartographer=cartographer, consult=consult, project_root=git_project)

    report = await scan.scan(focus="security", max_files=1)

    assert len(report["findings_per_file"]) == 1
    assert report["findings_per_file"][0]["file"] == "bad.py"
    assert report["findings_per_file"][0]["verdict"] == "FOUND"
