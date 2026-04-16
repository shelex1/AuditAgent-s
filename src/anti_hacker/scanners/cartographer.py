from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..council.aggregator import parse_member_json
from ..errors import AntiHackerError
from ..openrouter.client import OpenRouterClient
from .file_filter import iter_project_files


Focus = Literal["security", "quality", "perf", "all"]


@dataclass(frozen=True)
class FileRisk:
    path: Path
    risk_score: int
    summary: str


FOCUS_HINT = {
    "security": "Prioritize injection, auth, crypto, deserialization, and IO-facing code.",
    "quality": "Prioritize duplicated, tangled, or hard-to-test code.",
    "perf": "Prioritize hot paths, N+1 queries, synchronous blocking IO.",
    "all": "Score holistically across security, quality, and performance risks.",
}


def _build_prompt(tree_listing: str, focus: Focus) -> str:
    return (
        "You are a project cartographer. Given a listing of source files with their paths and "
        "first ~400 bytes of content, rank each file by risk_score 0-10 (10 = highest) for a "
        "follow-up deep review. " + FOCUS_HINT[focus] +
        "\n\nReply ONLY as a JSON object of shape:\n"
        '{"files": [{"file": "<path>", "risk_score": <int>, "summary": "<one line>"}]}\n\n'
        "Listing:\n" + tree_listing
    )


def _listing(root: Path, paths: list[Path], per_file_bytes: int = 400) -> str:
    parts = []
    for p in paths:
        try:
            head = p.read_bytes()[:per_file_bytes].decode("utf-8", errors="replace")
        except OSError:
            head = "<unreadable>"
        rel = p.relative_to(root).as_posix()
        parts.append(f"### {rel}\n```\n{head}\n```")
    return "\n".join(parts)


class Cartographer:
    def __init__(self, *, client: OpenRouterClient, model: str, timeout: float) -> None:
        self.client = client
        self.model = model
        self.timeout = timeout

    async def build_map(
        self,
        project_root: Path,
        *,
        max_files: int,
        focus: Focus,
    ) -> list[FileRisk]:
        candidates = list(iter_project_files(project_root))
        if not candidates:
            return []
        listing = _listing(project_root, candidates)
        prompt = _build_prompt(listing, focus)
        resp = await self.client.chat(
            model=self.model,
            system="You are a precise cartographer. Reply only as JSON.",
            user=prompt,
            timeout=self.timeout,
        )
        ok, payload, err = parse_member_json(resp.text)
        if not ok:
            raise AntiHackerError(f"cartographer returned malformed JSON: {err}")
        items = payload.get("files", []) or []

        by_name = {p.relative_to(project_root).as_posix(): p for p in candidates}
        ranked: list[FileRisk] = []
        for it in items:
            rel = str(it.get("file", "")).strip()
            if rel not in by_name:
                continue
            try:
                score = int(it.get("risk_score", 0))
            except (TypeError, ValueError):
                score = 0
            ranked.append(
                FileRisk(
                    path=by_name[rel],
                    risk_score=max(0, min(10, score)),
                    summary=str(it.get("summary", ""))[:200],
                )
            )

        ranked.sort(key=lambda r: r.risk_score, reverse=True)
        return ranked[:max_files]
