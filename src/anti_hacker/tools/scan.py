from __future__ import annotations

from pathlib import Path
from typing import Any

from ..scanners.cartographer import Cartographer, Focus
from .consult import ConsultService


class ScanService:
    def __init__(
        self,
        *,
        cartographer: Cartographer,
        consult: ConsultService,
        project_root: Path,
    ) -> None:
        self.cartographer = cartographer
        self.consult = consult
        self.project_root = project_root.resolve()

    async def scan(self, *, focus: Focus, max_files: int) -> dict[str, Any]:
        ranked = await self.cartographer.build_map(
            self.project_root, max_files=max_files, focus=focus
        )
        findings_per_file: list[dict[str, Any]] = []
        for fr in ranked:
            rel = fr.path.relative_to(self.project_root).as_posix()
            mode = "security" if focus == "security" else "review"
            report = await self.consult.consult(task=f"Deep review; focus={focus}", files=[rel], mode=mode)  # type: ignore[arg-type]
            findings_per_file.append(
                {
                    "file": rel,
                    "risk_score": fr.risk_score,
                    "cartographer_summary": fr.summary,
                    "verdict": report.get("verdict"),
                    "findings": report.get("findings", []),
                    "patch_file": report.get("patch_file", ""),
                    "debate_id": report.get("debate_id", ""),
                }
            )

        findings_per_file.sort(key=lambda r: r["risk_score"], reverse=True)
        return {
            "project_root": str(self.project_root),
            "focus": focus,
            "examined_files": len(ranked),
            "findings_per_file": findings_per_file,
        }
