from __future__ import annotations

from typing import Any

from .consult import ConsultService


class InvestigateService:
    def __init__(self, *, consult: ConsultService) -> None:
        self.consult = consult

    async def investigate(
        self,
        *,
        symptom: str,
        related_files: list[str],
        reproduction: str | None = None,
        stack_trace: str | None = None,
    ) -> dict[str, Any]:
        task_parts = [f"Bug investigation. Symptom: {symptom}"]
        if reproduction:
            task_parts.append(f"Reproduction: {reproduction}")
        if stack_trace:
            task_parts.append(f"Stack trace: {stack_trace}")
        task_parts.append(
            "Round 1: propose root-cause hypotheses. "
            "Round 2: critique each other. "
            "Round 3: final root cause + minimal fix patch."
        )
        task = "\n\n".join(task_parts)
        return await self.consult.consult(task=task, files=related_files, mode="free")
