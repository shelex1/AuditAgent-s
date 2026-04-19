from __future__ import annotations

from dataclasses import dataclass
import pytest

from anti_hacker.config import FallbackEntry, MemberConfig
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
    entry = FallbackEntry(provider="modal", model="fb/model")
    member = CouncilMember(
        config=_cfg(fallbacks=[entry]),
        primary_client=primary,
        fallback_chain=[(entry, fallback)],
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
    entry = FallbackEntry(provider="modal", model="fb/model")
    member = CouncilMember(
        config=_cfg(fallbacks=[entry]),
        primary_client=primary,
        fallback_chain=[(entry, fallback)],
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
    entry = FallbackEntry(provider="modal", model="fb/model")
    member = CouncilMember(
        config=_cfg(fallbacks=[entry]),
        primary_client=primary,
        fallback_chain=[(entry, fallback)],
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
    )
    with pytest.raises(OpenRouterError):
        await member.ask(system="s", user="u")


@pytest.mark.asyncio
async def test_rate_limit_fallback_also_fails_propagates():
    primary = FakeClient(raises=OpenRouterError("limit", kind="rate_limit"))
    fallback = FakeClient(raises=OpenRouterError("fb-limit", kind="rate_limit"))
    entry = FallbackEntry(provider="modal", model="fb/model")
    member = CouncilMember(
        config=_cfg(fallbacks=[entry]),
        primary_client=primary,
        fallback_chain=[(entry, fallback)],
    )
    with pytest.raises(OpenRouterError):
        await member.ask(system="s", user="u")


@pytest.mark.asyncio
async def test_chain_walks_past_rate_limited_link():
    primary = FakeClient(raises=OpenRouterError("rl", kind="rate_limit"))
    fb1 = FakeClient(raises=OpenRouterError("rl2", kind="rate_limit"))
    fb2 = FakeClient(text="third-ok")
    member = CouncilMember(
        config=_cfg(fallbacks=[
            FallbackEntry(provider="modal", model="fb1/model"),
            FallbackEntry(provider="ollama", model="fb2/model"),
        ]),
        primary_client=primary,
        fallback_chain=[
            (FallbackEntry(provider="modal", model="fb1/model"), fb1),
            (FallbackEntry(provider="ollama", model="fb2/model"), fb2),
        ],
    )
    reply = await member.ask(system="s", user="u")
    assert reply.text == "third-ok"
    assert reply.provider == "ollama"
    assert reply.model == "fb2/model"
    assert reply.via_fallback is True


@pytest.mark.asyncio
async def test_chain_walks_on_quota_exhausted():
    primary = FakeClient(raises=OpenRouterError("q", kind="quota_exhausted"))
    fb = FakeClient(text="fb-ok")
    entry = FallbackEntry(provider="modal", model="fb/model")
    member = CouncilMember(
        config=_cfg(fallbacks=[entry]),
        primary_client=primary,
        fallback_chain=[(entry, fb)],
    )
    reply = await member.ask(system="s", user="u")
    assert reply.provider == "modal"
    assert reply.via_fallback is True


@pytest.mark.asyncio
async def test_chain_does_not_walk_on_malformed():
    primary = FakeClient(raises=OpenRouterError("bad", kind="malformed"))
    fb = FakeClient(text="never-called")
    entry = FallbackEntry(provider="modal", model="fb/model")
    member = CouncilMember(
        config=_cfg(fallbacks=[entry]),
        primary_client=primary,
        fallback_chain=[(entry, fb)],
    )
    with pytest.raises(OpenRouterError) as exc:
        await member.ask(system="s", user="u")
    assert exc.value.kind == "malformed"
    assert fb.calls == []


@pytest.mark.asyncio
async def test_chain_exhausted_reraises_last():
    primary = FakeClient(raises=OpenRouterError("p-rl", kind="rate_limit"))
    fb = FakeClient(raises=OpenRouterError("fb-rl", kind="rate_limit"))
    entry = FallbackEntry(provider="modal", model="fb/model")
    member = CouncilMember(
        config=_cfg(fallbacks=[entry]),
        primary_client=primary,
        fallback_chain=[(entry, fb)],
    )
    with pytest.raises(OpenRouterError) as exc:
        await member.ask(system="s", user="u")
    assert exc.value.kind == "rate_limit"
    assert "fb-rl" in str(exc.value)
