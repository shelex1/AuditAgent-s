from __future__ import annotations

from pathlib import Path
from typing import Any

from ..io.debate_log import load_debate_log
from ..io.proposals import list_pending_proposals


class LogService:
    def __init__(self, *, data_root: Path) -> None:
        self.data_root = data_root.resolve()

    def get_debate_log(self, debate_id: str) -> dict[str, Any]:
        return load_debate_log(debate_id, root=self.data_root)

    def list_proposals(self) -> list[dict[str, Any]]:
        return list_pending_proposals(root=self.data_root)
