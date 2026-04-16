from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from ..council.member import CouncilMember
from ..council.aggregator import parse_member_json
from ..council.prompts import (
    Mode,
    build_round1_prompt,
    build_round2_prompt,
    build_round3_prompt,
    role_system_prompt,
)
from ..errors import OpenRouterError

logger = logging.getLogger(__name__)


@dataclass
class RoundResult:
    round1: dict[str, dict[str, Any]] = field(default_factory=dict)
    round2: dict[str, dict[str, Any]] = field(default_factory=dict)
    round3: dict[str, dict[str, Any]] = field(default_factory=dict)
    abstained: list[str] = field(default_factory=list)
    partial_timeout: bool = False
    errors: dict[str, str] = field(default_factory=dict)


REPAIR_INSTRUCTION = (
    "Your previous response was NOT valid JSON. "
    "Here is what you returned: <<{raw}>>. "
    "Return ONLY a valid JSON object matching the schema. No prose."
)


class DebateOrchestra:
    def __init__(
        self,
        *,
        members: list[CouncilMember],
        debate_timeout: float,
        max_bytes_per_file: int,
    ) -> None:
        self.members = members
        self.debate_timeout = debate_timeout
        self.max_bytes_per_file = max_bytes_per_file

    async def run(self, *, task: str, files: dict[str, str], mode: Mode) -> RoundResult:
        result = RoundResult()
        try:
            await asyncio.wait_for(
                self._run_inner(task, files, mode, result),
                timeout=self.debate_timeout,
            )
        except asyncio.TimeoutError:
            result.partial_timeout = True
        return result

    async def _run_inner(
        self,
        task: str,
        files: dict[str, str],
        mode: Mode,
        result: RoundResult,
    ) -> None:
        active = list(self.members)

        # ROUND 1
        r1_user = build_round1_prompt(task=task, files=files, mode=mode, max_bytes_per_file=self.max_bytes_per_file)
        r1 = await self._ask_all(active, r1_user, result)
        result.round1 = r1
        active = [m for m in active if m.name in r1]

        if len(active) < 3:
            return

        # ROUND 2
        peer_list = [{"member": n, "payload": p} for n, p in r1.items()]
        r2_user = build_round2_prompt(task=task, peer_responses=peer_list)
        r2 = await self._ask_all(active, r2_user, result)
        result.round2 = r2
        active = [m for m in active if m.name in r2]

        if len(active) < 3:
            return

        # ROUND 3
        r1_list = [{"member": n, "payload": p} for n, p in r1.items() if n in {m.name for m in active}]
        r2_list = [{"member": n, "payload": p} for n, p in r2.items()]
        r3_user = build_round3_prompt(
            task=task,
            round1=r1_list,
            round2=r2_list,
            files=files,
            max_bytes_per_file=self.max_bytes_per_file,
        )
        r3 = await self._ask_all(active, r3_user, result)
        result.round3 = r3

    async def _ask_all(
        self,
        members: list[CouncilMember],
        user_prompt: str,
        result: RoundResult,
    ) -> dict[str, dict[str, Any]]:
        async def _one(member: CouncilMember) -> tuple[str, dict[str, Any] | None, str]:
            system = role_system_prompt(member.role)
            try:
                raw = await member.ask(system=system, user=user_prompt)
            except OpenRouterError as exc:
                return member.name, None, f"openrouter: {exc}"
            ok, payload, err = parse_member_json(raw)
            if not ok:
                # repair retry
                try:
                    raw2 = await member.ask(
                        system=system,
                        user=REPAIR_INSTRUCTION.format(raw=raw[:500]),
                    )
                except OpenRouterError as exc:
                    return member.name, None, f"openrouter(repair): {exc}"
                ok2, payload2, err2 = parse_member_json(raw2)
                if not ok2:
                    return member.name, None, f"invalid_json: {err2}"
                return member.name, payload2, ""
            return member.name, payload, ""

        tasks = [asyncio.create_task(_one(m)) for m in members]
        out: dict[str, dict[str, Any]] = {}
        for coro in asyncio.as_completed(tasks):
            name, payload, err = await coro
            if payload is None:
                if name not in result.abstained:
                    result.abstained.append(name)
                result.errors[name] = err
                logger.warning("member %s abstained: %s", name, err)
            else:
                out[name] = payload
        return out
