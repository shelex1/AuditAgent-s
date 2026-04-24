from __future__ import annotations

import logging
from typing import Any


_LOG_THOUGHT_TRUNCATE = 120


class ThinkingService:
    def __init__(self) -> None:
        self._history: list[dict[str, Any]] = []
        self._branches: dict[str, list[dict[str, Any]]] = {}
        self._logger = logging.getLogger("anti_hacker.thinking")

    def process_thought(
        self,
        *,
        thought: str,
        thought_number: int,
        total_thoughts: int,
        next_thought_needed: bool,
        is_revision: bool | None = None,
        revises_thought: int | None = None,
        branch_from_thought: int | None = None,
        branch_id: str | None = None,
        needs_more_thoughts: bool | None = None,
    ) -> dict[str, Any]:
        if thought_number > total_thoughts:
            total_thoughts = thought_number

        snapshot: dict[str, Any] = {
            "thought": thought,
            "thought_number": thought_number,
            "total_thoughts": total_thoughts,
            "next_thought_needed": next_thought_needed,
        }
        if is_revision is not None:
            snapshot["is_revision"] = is_revision
        if revises_thought is not None:
            snapshot["revises_thought"] = revises_thought
        if branch_from_thought is not None:
            snapshot["branch_from_thought"] = branch_from_thought
        if branch_id is not None:
            snapshot["branch_id"] = branch_id
        if needs_more_thoughts is not None:
            snapshot["needs_more_thoughts"] = needs_more_thoughts

        self._history.append(snapshot)

        if branch_from_thought is not None and branch_id is not None:
            self._branches.setdefault(branch_id, []).append(snapshot)

        self._logger.info(self._format_log(snapshot))

        return {
            "thought_number": thought_number,
            "total_thoughts": total_thoughts,
            "next_thought_needed": next_thought_needed,
            "branches": list(self._branches.keys()),
            "thought_history_length": len(self._history),
        }

    def get_history(self, branch_id: str | None = None) -> dict[str, Any]:
        if branch_id is not None:
            branch = self._branches.get(branch_id, [])
            return {"history": list(branch), "total": len(branch)}
        return {
            "history": list(self._history),
            "branches": {bid: list(items) for bid, items in self._branches.items()},
            "total": len(self._history),
        }

    @staticmethod
    def _format_log(snapshot: dict[str, Any]) -> str:
        n = snapshot["thought_number"]
        m = snapshot["total_thoughts"]
        text = snapshot["thought"]
        if len(text) > _LOG_THOUGHT_TRUNCATE:
            text = text[:_LOG_THOUGHT_TRUNCATE] + "…"
        quoted = f'"{text}"'

        if snapshot.get("is_revision"):
            k = snapshot.get("revises_thought", "?")
            return f"🔄 revise {n}→{k} {quoted}"
        if snapshot.get("branch_from_thought") is not None:
            bid = snapshot.get("branch_id", "?")
            return f"🌿 {bid} {n}/{m} {quoted}"
        return f"💭 thought {n}/{m} {quoted}"
