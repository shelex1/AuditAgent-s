import subprocess
from pathlib import Path

import pytest

from anti_hacker.io.proposals import (
    ProposalStore,
    validate_patch,
    list_pending_proposals,
)


VALID_DIFF = """--- a/hello.py
+++ b/hello.py
@@ -1,1 +1,1 @@
-print("hi")
+print("hello")
"""

BROKEN_DIFF = "this is not a diff at all"


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "hello.py").write_text('print("hi")\n', encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_validate_valid_patch(git_repo: Path) -> None:
    ok, err = validate_patch(VALID_DIFF, project_root=git_repo)
    assert ok
    assert err == ""


def test_validate_broken_patch(git_repo: Path) -> None:
    ok, err = validate_patch(BROKEN_DIFF, project_root=git_repo)
    assert not ok
    assert err  # some error message


def test_save_valid_patch(tmp_project: Path, git_repo: Path) -> None:
    store = ProposalStore(root=tmp_project)
    path = store.save(debate_id="d1", unified_diff=VALID_DIFF, metadata={"summary": "x"})
    assert path.exists()
    assert path.suffix == ".patch"
    assert "hello.py" in path.read_text(encoding="utf-8")
    # metadata sidecar
    meta = path.with_suffix(".meta.json")
    assert meta.exists()


def test_list_pending(tmp_project: Path) -> None:
    store = ProposalStore(root=tmp_project)
    store.save(debate_id="d1", unified_diff=VALID_DIFF, metadata={"summary": "a"})
    store.save(debate_id="d2", unified_diff=VALID_DIFF, metadata={"summary": "b"})
    pending = list_pending_proposals(root=tmp_project)
    ids = {p["debate_id"] for p in pending}
    assert ids == {"d1", "d2"}
