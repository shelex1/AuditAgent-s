from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import FallbackEntry, MemberConfig, Role
from ..errors import OpenRouterError


FALLBACK_TRIGGERS: frozenset[str] = frozenset({"rate_limit", "quota_exhausted"})


class ChatClient(Protocol):
    async def chat(
        self,
        *,
        model: str,
        system: str,
        user: str,
        timeout: float,
        max_retries: int | None = None,
        response_format_json: bool = True,
    ) -> Any: ...


@dataclass(frozen=True)
class MemberReply:
    text: str
    provider: str
    via_fallback: bool
    model: str


@dataclass
class CouncilMember:
    config: MemberConfig
    primary_client: ChatClient
    fallback_chain: list[tuple[FallbackEntry, ChatClient]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def role(self) -> Role:
        return self.config.role

    async def ask(self, *, system: str, user: str) -> MemberReply:
        timeout = float(self.config.timeout)
        try:
            resp = await self.primary_client.chat(
                model=self.config.model,
                system=system,
                user=user,
                timeout=timeout,
            )
            return MemberReply(
                text=resp.text,
                provider=self.config.provider or "",
                via_fallback=False,
                model=self.config.model,
            )
        except OpenRouterError as exc:
            if exc.kind not in FALLBACK_TRIGGERS:
                raise
            last: OpenRouterError = exc
            for entry, client in self.fallback_chain:
                try:
                    fb_resp = await client.chat(
                        model=entry.model,
                        system=system,
                        user=user,
                        timeout=timeout,
                        max_retries=1,
                    )
                    return MemberReply(
                        text=fb_resp.text,
                        provider=entry.provider,
                        via_fallback=True,
                        model=entry.model,
                    )
                except OpenRouterError as exc2:
                    if exc2.kind in FALLBACK_TRIGGERS:
                        last = exc2
                        continue
                    raise
            raise last
