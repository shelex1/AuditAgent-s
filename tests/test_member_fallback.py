from __future__ import annotations

from dataclasses import dataclass
import pytest

from anti_hacker.config import MemberConfig
from anti_hacker.council.member import CouncilMember, MemberReply
from anti_hacker.errors import OpenRouterError


@dataclass
class FakeResp:
    text: str
    model: str


class FakeClient:
    def __init__(self, *, raises: Exception | None = None, text: str = "ok"):
        self.raises = raises
        self.text = text
        self.calls: list[dict] = []

    async def chat(self, *, model, system, user, timeout, max_retries=None, **_):
        self.calls.append({"model": model, "timeout": timeout, "max_retries": max_retries})
        if self.raises is not None:
            raise self.raises
        return FakeResp(text=self.text, model=model)


def _cfg(**overrides) -> MemberConfig:
    base = dict(
        name="m1",
        model="primary/model",
        role="security-paranoid",
        timeout=30,
        provider="openrouter",
    )
    base.update(overrides)
    return MemberConfig(**base)


@pytest.mark.asyncio
async def test_primary_success_no_fallback_call():
    primary = FakeClient(text="primary-ok")
    fallback = FakeClient(text="fallback-ok")
    member = CouncilMember(
        config=_cfg(fallback_provider="modal", fallback_model="fb/model"),
        primary_client=primary,
        fallback_client=fallback,
    )
    reply = await member.ask(system="s", user="u")
    assert isinstance(reply, MemberReply)
    assert reply.text == "primary-ok"
    assert reply.provider == "openrouter"
    assert reply.via_fallback is False
    assert len(primary.calls) == 1
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_rate_limit_triggers_fallback():
    primary = FakeClient(raises=OpenRouterError("limit", kind="rate_limit"))
    fallback = FakeClient(text="fallback-ok")
    member = CouncilMember(
        config=_cfg(fallback_provider="modal", fallback_model="fb/model"),
        primary_client=primary,
        fallback_client=fallback,
    )
    reply = await member.ask(system="s", user="u")
    assert reply.text == "fallback-ok"
    assert reply.provider == "modal"
    assert reply.via_fallback is True
    assert fallback.calls[0]["model"] == "fb/model"
    assert fallback.calls[0]["max_retries"] == 1


@pytest.mark.asyncio
async def test_timeout_does_not_trigger_fallback():
    primary = FakeClient(raises=OpenRouterError("slow", kind="timeout"))
    fallback = FakeClient(text="unused")
    member = CouncilMember(
        config=_cfg(fallback_provider="modal", fallback_model="fb/model"),
        primary_client=primary,
        fallback_client=fallback,
    )
    with pytest.raises(OpenRouterError) as ei:
        await member.ask(system="s", user="u")
    assert ei.value.kind == "timeout"
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_rate_limit_no_fallback_configured_propagates():
    primary = FakeClient(raises=OpenRouterError("limit", kind="rate_limit"))
    member = CouncilMember(
        config=_cfg(),  # no fallback
        primary_client=primary,
        fallback_client=None,
    )
    with pytest.raises(OpenRouterError):
        await member.ask(system="s", user="u")


@pytest.mark.asyncio
async def test_rate_limit_fallback_also_fails_propagates():
    primary = FakeClient(raises=OpenRouterError("limit", kind="rate_limit"))
    fallback = FakeClient(raises=OpenRouterError("fb-limit", kind="rate_limit"))
    member = CouncilMember(
        config=_cfg(fallback_provider="modal", fallback_model="fb/model"),
        primary_client=primary,
        fallback_client=fallback,
    )
    with pytest.raises(OpenRouterError):
        await member.ask(system="s", user="u")
