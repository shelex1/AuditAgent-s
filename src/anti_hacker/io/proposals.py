from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def validate_patch(unified_diff: str, *, project_root: Path) -> tuple[bool, str]:
    """Run `git apply --check` on the patch against project_root.

    Returns (ok, error_message). Requires a git repo at project_root.
    """
    if not unified_diff.strip():
        return False, "empty patch"
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as f:
        f.write(unified_diff)
        patch_path = f.name
    try:
        r = subprocess.run(
            ["git", "apply", "--check", patch_path],
            cwd=project_root,
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            return True, ""
        return False, (r.stderr or r.stdout).strip()
    finally:
        os.unlink(patch_path)


class ProposalStore:
    def __init__(self, *, root: Path) -> None:
        self.root = root
        self._dir = root / "council_proposals"
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, *, debate_id: str, unified_diff: str, metadata: dict[str, Any]) -> Path:
        target = self._dir / f"{debate_id}.patch"
        meta = self._dir / f"{debate_id}.meta.json"
        self._atomic_write(target, unified_diff)
        self._atomic_write(meta, json.dumps(metadata, indent=2, ensure_ascii=False))
        return target

    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, target)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise


def list_pending_proposals(*, root: Path) -> list[dict[str, Any]]:
    pdir = root / "council_proposals"
    if not pdir.exists():
        return []
    out = []
    for patch in sorted(pdir.glob("*.patch")):
        meta_file = patch.with_suffix(".meta.json")
        metadata: dict[str, Any] = {}
        if meta_file.exists():
            try:
                metadata = json.loads(meta_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                metadata = {}
        out.append(
            {
                "debate_id": patch.stem,
                "patch_path": str(patch),
                "metadata": metadata,
            }
        )
    return out
