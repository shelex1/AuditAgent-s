from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Config
from ..council.aggregator import aggregate
from ..council.cache import DebateCache
from ..council.member import CouncilMember
from ..council.orchestra import DebateOrchestra
from ..council.prompts import Mode
from ..io.debate_log import DebateLog
from ..io.proposals import ProposalStore, validate_patch
from ..openrouter.client import OpenRouterClient
from ..scanners.file_filter import path_is_under

logger = logging.getLogger(__name__)


def _debate_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S_")
    suffix = uuid.uuid4().hex[:4]
    return ts + suffix


class ConsultService:
    def __init__(
        self,
        *,
        config: Config,
        client: OpenRouterClient,
        cache: DebateCache,
        project_root: Path,
        data_root: Path,
    ) -> None:
        self.config = config
        self.client = client
        self.cache = cache
        self.project_root = project_root.resolve()
        self.data_root = data_root.resolve()
        self.proposals = ProposalStore(root=data_root)

    def _read_files(self, rels: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for rel in rels:
            p = (self.project_root / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
            if not path_is_under(p, self.project_root):
                raise ValueError(f"path outside project root: {rel}")
            if not p.exists():
                raise FileNotFoundError(f"file not found: {rel}")
            out[p.relative_to(self.project_root).as_posix()] = p.read_text(encoding="utf-8", errors="replace")
        return out

    async def consult(
        self,
        *,
        task: str,
        files: list[str],
        mode: Mode,
        force_fresh: bool = False,
    ) -> dict[str, Any]:
        file_contents = self._read_files(files)

        key = self.cache.make_key(task=task, files=file_contents, mode=mode)
        if not force_fresh:
            hit = self.cache.get(key)
            if hit is not None:
                return {**hit, "cached": True}

        members = [CouncilMember(config=mc, client=self.client) for mc in self.config.members]
        orchestra = DebateOrchestra(
            members=members,
            debate_timeout=self.config.limits.debate_timeout,
            max_bytes_per_file=self.config.limits.max_file_size_bytes,
        )
        result = await orchestra.run(task=task, files=file_contents, mode=mode)

        debate_id = _debate_id()
        log = DebateLog(debate_id=debate_id, root=self.data_root)
        log.record_round(1, result.round1)
        log.record_round(2, result.round2)
        log.record_round(3, result.round3)

        if not result.round3 or len(result.round3) < 3:
            if result.partial_timeout:
                status = "partial_timeout"
            else:
                status = "quorum_lost"
            report: dict[str, Any] = {
                "debate_id": debate_id,
                "verdict": "QUORUM_LOST",
                "status": status,
                "abstained": result.abstained,
                "errors": result.errors,
                "full_log": "",
                "patch_file": "",
                "alternative_patches": 0,
                "log_persisted": True,
            }
            try:
                log_path = log.finalize(report)
                report["full_log"] = str(log_path)
            except OSError:
                report["log_persisted"] = False
            self.cache.put(key, report)
            return report

        agg = aggregate(round3=result.round3, total_members=len(self.config.members))

        patch_file = ""
        alternatives = 0
        if agg.winning_patch:
            ok, err = validate_patch(agg.winning_patch, project_root=self.project_root)
            if ok:
                path = self.proposals.save(
                    debate_id=debate_id,
                    unified_diff=agg.winning_patch,
                    metadata={
                        "summary": f"{agg.verdict}: {len(agg.findings)} finding(s)",
                        "task": task,
                        "files": list(file_contents.keys()),
                    },
                )
                patch_file = str(path)
            else:
                logger.warning("winning patch failed git apply --check: %s", err)

        # validate alternatives, too
        valid_alts: list[str] = []
        for alt in agg.alternative_patches:
            ok, _ = validate_patch(alt, project_root=self.project_root)
            if ok:
                valid_alts.append(alt)
        alternatives = len(valid_alts)

        report = {
            "debate_id": debate_id,
            "verdict": agg.verdict,
            "confidence": agg.confidence,
            "findings": agg.findings,
            "patch_file": patch_file,
            "alternative_patches": alternatives,
            "full_log": "",
            "log_persisted": True,
            "status": "partial_timeout" if result.partial_timeout else "success",
            "abstained": result.abstained,
        }

        try:
            log_path = log.finalize(report)
            report["full_log"] = str(log_path)
        except OSError:
            report["log_persisted"] = False

        self.cache.put(key, report)
        return report
