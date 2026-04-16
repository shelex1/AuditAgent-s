from __future__ import annotations

from dataclasses import dataclass

from ..config import MemberConfig, Role
from ..openrouter.client import OpenRouterClient


@dataclass
class CouncilMember:
    config: MemberConfig
    client: OpenRouterClient

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def role(self) -> Role:
        return self.config.role

    async def ask(self, *, system: str, user: str) -> str:
        resp = await self.client.chat(
            model=self.config.model,
            system=system,
            user=user,
            timeout=float(self.config.timeout),
        )
        return resp.text
