from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..config import MemberConfig, Role
from ..errors import OpenRouterError


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
    fallback_client: ChatClient | None = None

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
            if (
                exc.kind == "rate_limit"
                and self.fallback_client is not None
                and self.config.fallback_provider is not None
                and self.config.fallback_model is not None
            ):
                fb_resp = await self.fallback_client.chat(
                    model=self.config.fallback_model,
                    system=system,
                    user=user,
                    timeout=timeout,
                    max_retries=1,
                )
                return MemberReply(
                    text=fb_resp.text,
                    provider=self.config.fallback_provider,
                    via_fallback=True,
                    model=self.config.fallback_model,
                )
            raise
