import asyncio

import httpx
import pytest

from anti_hacker.errors import OpenRouterError
from anti_hacker.openrouter.client import OpenRouterClient, OpenRouterResponse
from tests.fixtures.sample_responses import chat_completion


def _transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_successful_call_returns_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(200, json=chat_completion("hello"))

    client = OpenRouterClient(
        api_key="sk-test",
        base_url="https://openrouter.ai/api/v1",
        transport=_transport(handler),
    )
    resp = await client.chat(model="x/y:free", system="s", user="u", timeout=10)
    assert isinstance(resp, OpenRouterResponse)
    assert resp.text == "hello"


@pytest.mark.asyncio
async def test_rate_limit_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "rate limited"})
        return httpx.Response(200, json=chat_completion("ok"))

    client = OpenRouterClient(
        api_key="sk-test",
        base_url="https://openrouter.ai/api/v1",
        transport=_transport(handler),
        retry_backoff=lambda attempt: 0,  # no real sleep in tests
    )
    resp = await client.chat(model="x", system="s", user="u", timeout=10)
    assert resp.text == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_rate_limit_exhausts_retries_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "rate limited"})

    client = OpenRouterClient(
        api_key="sk-test",
        base_url="https://openrouter.ai/api/v1",
        transport=_transport(handler),
        retry_backoff=lambda attempt: 0,
    )
    with pytest.raises(OpenRouterError, match="rate limit"):
        await client.chat(model="x", system="s", user="u", timeout=10)


@pytest.mark.asyncio
async def test_5xx_retries_then_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    client = OpenRouterClient(
        api_key="sk-test",
        base_url="https://openrouter.ai/api/v1",
        transport=_transport(handler),
        retry_backoff=lambda attempt: 0,
    )
    with pytest.raises(OpenRouterError):
        await client.chat(model="x", system="s", user="u", timeout=10)


@pytest.mark.asyncio
async def test_network_error_raises_openrouter_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    client = OpenRouterClient(
        api_key="sk-test",
        base_url="https://openrouter.ai/api/v1",
        transport=_transport(handler),
        retry_backoff=lambda attempt: 0,
    )
    with pytest.raises(OpenRouterError, match="network"):
        await client.chat(model="x", system="s", user="u", timeout=10)


@pytest.mark.asyncio
async def test_per_call_timeout_raises() -> None:
    async def slow_handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(200, json=chat_completion("late"))

    client = OpenRouterClient(
        api_key="sk-test",
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(slow_handler),
        retry_backoff=lambda attempt: 0,
    )
    with pytest.raises(OpenRouterError, match="timeout"):
        await client.chat(model="x", system="s", user="u", timeout=0.05)
