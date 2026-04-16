import json
import subprocess
from pathlib import Path

import httpx
import pytest

from anti_hacker.config import Config, MemberConfig, CartographerConfig, LimitsConfig, OpenRouterConfig
from anti_hacker.council.cache import DebateCache
from anti_hacker.openrouter.client import OpenRouterClient
from anti_hacker.tools.consult import ConsultService
from tests.fixtures.sample_responses import chat_completion


VULN_FILE = '''import os

def run(user_input):
    os.system(user_input)
'''


R1 = json.dumps({
    "findings": [{"line": 4, "severity": "critical", "description": "shell injection via os.system(user_input)", "proposed_fix": "use subprocess.run with list args"}],
    "confidence": 10,
    "reasoning": "direct concat to shell",
})
R2 = json.dumps({"agree_with": ["shell injection via os.system(user_input)"], "disagree_with": [], "missed_findings": [], "updated_confidence": 10})
R3 = json.dumps({
    "final_findings": [{"line": 4, "severity": "critical", "description": "shell injection via os.system(user_input)"}],
    "unified_patch": "--- a/vuln.py\n+++ b/vuln.py\n@@ -1,4 +1,5 @@\n import os\n+import subprocess\n \n def run(user_input):\n-    os.system(user_input)\n+    subprocess.run(user_input, shell=False, check=True)\n",
    "final_confidence": 10,
})


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "vuln.py").write_text(VULN_FILE, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _config() -> Config:
    return Config(
        api_key="sk-test",
        members=[MemberConfig(name=f"m{i}", model=f"p/m{i}:free", role="security-paranoid", timeout=10) for i in range(1, 6)],
        cartographer=CartographerConfig(model="p/fast:free", timeout=60),
        limits=LimitsConfig(),
        openrouter=OpenRouterConfig(),
    )


@pytest.mark.asyncio
async def test_e2e_finds_shell_injection_and_writes_applicable_patch(git_project: Path) -> None:
    cfg = _config()
    sequence = [R1] * 5 + [R2] * 5 + [R3] * 5
    counter = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = counter["i"]
        counter["i"] += 1
        return httpx.Response(200, json=chat_completion(sequence[min(i, len(sequence) - 1)]))

    client = OpenRouterClient(api_key=cfg.api_key, base_url=cfg.openrouter.base_url, transport=httpx.MockTransport(handler), retry_backoff=lambda a: 0)
    cache = DebateCache(ttl_seconds=0)
    service = ConsultService(config=cfg, client=client, cache=cache, project_root=git_project, data_root=git_project)

    report = await service.consult(task="find security flaws", files=["vuln.py"], mode="security")

    assert report["verdict"] == "FOUND"
    assert report["findings"][0]["severity"] in {"high", "critical"}
    patch_path = Path(report["patch_file"])
    assert patch_path.exists()

    # git apply the patch and verify the file changed
    r = subprocess.run(["git", "apply", str(patch_path)], cwd=git_project, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "subprocess.run" in (git_project / "vuln.py").read_text(encoding="utf-8")

    # debate log fully written
    log = Path(report["full_log"])
    assert log.exists()
    payload = json.loads(log.read_text(encoding="utf-8"))
    assert len(payload["rounds"]) == 3

    # compact report is small
    compact = json.dumps(report, ensure_ascii=False)
    assert len(compact) < 4000  # generous bound; target is ~500-1500
