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
    member_meta: dict[int, dict[str, dict[str, Any]]] = field(default_factory=dict)
    # shape: {round_number: {member_name: {"provider": str, "via_fallback": bool, "model": str}}}


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
        r1 = await self._ask_all(active, r1_user, result, round_number=1)
        result.round1 = r1
        active = [m for m in active if m.name in r1]

        if len(active) < 3:
            return

        # ROUND 2
        peer_list = [{"member": n, "payload": p} for n, p in r1.items()]
        r2_user = build_round2_prompt(task=task, peer_responses=peer_list)
        r2 = await self._ask_all(active, r2_user, result, round_number=2)
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
        r3 = await self._ask_all(active, r3_user, result, round_number=3)
        result.round3 = r3

    async def _ask_all(
        self,
        members: list[CouncilMember],
        user_prompt: str,
        result: RoundResult,
        *,
        round_number: int,
    ) -> dict[str, dict[str, Any]]:
        async def _one(
            member: CouncilMember,
        ) -> tuple[str, dict[str, Any] | None, str, dict[str, Any] | None]:
            system = role_system_prompt(member.role)
            reply_meta: dict[str, Any] | None = None
            try:
                reply = await member.ask(system=system, user=user_prompt)
                reply_meta = {
                    "provider": reply.provider,
                    "via_fallback": reply.via_fallback,
                    "model": reply.model,
                }
                raw = reply.text
            except OpenRouterError as exc:
                return member.name, None, str(exc), None
            ok, payload, err = parse_member_json(raw)
            if not ok:
                # repair retry
                try:
                    reply2 = await member.ask(
                        system=system,
                        user=REPAIR_INSTRUCTION.format(raw=raw[:500]),
                    )
                    reply_meta = {
                        "provider": reply2.provider,
                        "via_fallback": reply2.via_fallback,
                        "model": reply2.model,
                    }
                    raw2 = reply2.text
                except OpenRouterError as exc:
                    return member.name, None, f"repair: {exc}", None
                ok2, payload2, err2 = parse_member_json(raw2)
                if not ok2:
                    return member.name, None, f"invalid_json: {err2}", None
                return member.name, payload2, "", reply_meta
            return member.name, payload, "", reply_meta

        tasks = [asyncio.create_task(_one(m)) for m in members]
        out: dict[str, dict[str, Any]] = {}
        meta_bucket = result.member_meta.setdefault(round_number, {})
        for coro in asyncio.as_completed(tasks):
            name, payload, err, meta = await coro
            if payload is None:
                if name not in result.abstained:
                    result.abstained.append(name)
                result.errors[name] = err
                logger.warning("member %s abstained: %s", name, err)
            else:
                out[name] = payload
                if meta is not None:
                    meta_bucket[name] = meta
        return out
