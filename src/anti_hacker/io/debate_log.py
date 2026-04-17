from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DebateLog:
    """Append rounds in-memory, then atomically write the full log on finalize."""

    def __init__(self, *, debate_id: str, root: Path) -> None:
        self.debate_id = debate_id
        self.root = root
        self._rounds: list[dict[str, Any]] = []
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._dir = root / "debates"
        self._dir.mkdir(parents=True, exist_ok=True)

    def record_round(
        self,
        round_number: int,
        responses: dict[str, Any],
        meta: dict[str, Any] | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "round": round_number,
            "at": datetime.now(timezone.utc).isoformat(),
            "responses": responses,
        }
        if meta is not None:
            entry["member_meta"] = meta
        self._rounds.append(entry)

    def finalize(self, final_report: dict[str, Any]) -> Path:
        payload = {
            "debate_id": self.debate_id,
            "started_at": self._started_at,
            "finalized_at": datetime.now(timezone.utc).isoformat(),
            "rounds": self._rounds,
            "final": final_report,
        }
        target = self._dir / f"{self.debate_id}.json"
        # atomic write: tempfile in same dir + os.replace
        fd, tmp_name = tempfile.mkstemp(prefix=f".{self.debate_id}.", suffix=".tmp", dir=self._dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.replace(tmp_name, target)
        except Exception:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
            raise
        return target


def load_debate_log(debate_id: str, *, root: Path) -> dict[str, Any]:
    target = root / "debates" / f"{debate_id}.json"
    if not target.exists():
        raise FileNotFoundError(f"debate log not found: {target}")
    return json.loads(target.read_text(encoding="utf-8"))
