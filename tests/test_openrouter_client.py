import asyncio
import json

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


# ---------------------------------------------------------------------------
# Fix 1: response_format fallback on HTTP 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_format_fallback_on_400() -> None:
    """Model returns 400 because it doesn't support response_format=json_object;
    client retries same attempt without that field and succeeds."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": "unsupported parameter"})
        return httpx.Response(200, json=chat_completion("ok"))

    client = OpenRouterClient(
        api_key="sk-test",
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(handler),
        retry_backoff=lambda a: 0,
    )
    resp = await client.chat(model="x", system="s", user="u", timeout=10)
    assert resp.text == "ok"
    assert len(calls) == 2  # one with response_format, one without
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


@pytest.mark.asyncio
async def test_response_format_disabled_does_not_send_field() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=chat_completion("ok"))

    client = OpenRouterClient(
        api_key="sk-test",
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(handler),
        retry_backoff=lambda a: 0,
    )
    await client.chat(model="x", system="s", user="u", timeout=10, response_format_json=False)
    assert "response_format" not in seen["body"]


@pytest.mark.asyncio
async def test_persistent_400_even_without_json_mode_is_non_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "really broken"})

    client = OpenRouterClient(
        api_key="sk-test",
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(handler),
        retry_backoff=lambda a: 0,
    )
    with pytest.raises(OpenRouterError, match="400"):
        await client.chat(model="x", system="s", user="u", timeout=10)


@pytest.mark.asyncio
async def test_empty_choices_raises_malformed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    client = OpenRouterClient(
        api_key="sk-test",
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(handler),
        retry_backoff=lambda a: 0,
    )
    with pytest.raises(OpenRouterError, match="malformed"):
        await client.chat(model="x", system="s", user="u", timeout=10)


# ---------------------------------------------------------------------------
# Fix 3: respect Retry-After header on 429
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_429_honors_retry_after() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(asyncio.get_event_loop().time())
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0.02"}, json={"e": "rl"})
        return httpx.Response(200, json=chat_completion("ok"))

    client = OpenRouterClient(
        api_key="sk-test",
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(handler),
        retry_backoff=lambda a: 10.0,  # would be 10s; Retry-After should override
    )
    resp = await client.chat(model="x", system="s", user="u", timeout=5)
    assert resp.text == "ok"
    # The second call was after retry-after (~0.02s), NOT after retry_backoff (10s)
    assert calls[1] - calls[0] < 1.0


@pytest.mark.asyncio
async def test_429_without_retry_after_uses_default_backoff() -> None:
    calls = {"n": 0}
    backoff_called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"e": "rl"})  # no Retry-After
        return httpx.Response(200, json=chat_completion("ok"))

    def backoff(attempt: int) -> float:
        backoff_called["n"] += 1
        return 0

    client = OpenRouterClient(
        api_key="sk-test",
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(handler),
        retry_backoff=backoff,
    )
    resp = await client.chat(model="x", system="s", user="u", timeout=5)
    assert resp.text == "ok"
    assert backoff_called["n"] >= 1  # default backoff was consulted


def test_openrouter_error_has_kind_default_none():
    from anti_hacker.errors import OpenRouterError
    err = OpenRouterError("boom")
    assert err.kind is None

def test_openrouter_error_kind_can_be_set():
    from anti_hacker.errors import OpenRouterError
    err = OpenRouterError("rate limited", kind="rate_limit")
    assert err.kind == "rate_limit"
    assert str(err) == "rate limited"


def _mock_transport(responses):
    call = {"i": 0}

    async def handler(request):
        i = call["i"]
        call["i"] += 1
        status, body, headers = responses[min(i, len(responses) - 1)]
        return httpx.Response(status, json=body, headers=headers or {})

    return httpx.MockTransport(handler), call


@pytest.mark.asyncio
async def test_429_terminal_sets_kind_rate_limit():
    transport, _ = _mock_transport([(429, {}, {"retry-after": "0"})] * 3)
    client = OpenRouterClient(
        api_key="k", base_url="http://x", transport=transport,
        retry_backoff=lambda a: 0.0, max_retries=3,
    )
    with pytest.raises(OpenRouterError) as ei:
        await client.chat(model="m", system="s", user="u", timeout=1.0)
    assert ei.value.kind == "rate_limit"


@pytest.mark.asyncio
async def test_5xx_terminal_sets_kind_upstream():
    transport, _ = _mock_transport([(503, {}, None)] * 3)
    client = OpenRouterClient(
        api_key="k", base_url="http://x", transport=transport,
        retry_backoff=lambda a: 0.0, max_retries=3,
    )
    with pytest.raises(OpenRouterError) as ei:
        await client.chat(model="m", system="s", user="u", timeout=1.0)
    assert ei.value.kind == "upstream"


@pytest.mark.asyncio
async def test_timeout_sets_kind_timeout():
    async def handler(request):
        raise httpx.TimeoutException("slow")
    transport = httpx.MockTransport(handler)
    client = OpenRouterClient(
        api_key="k", base_url="http://x", transport=transport,
        retry_backoff=lambda a: 0.0, max_retries=2,
    )
    with pytest.raises(OpenRouterError) as ei:
        await client.chat(model="m", system="s", user="u", timeout=1.0)
    assert ei.value.kind == "timeout"


@pytest.mark.asyncio
async def test_per_call_max_retries_overrides_instance():
    transport, counter = _mock_transport([(429, {}, {"retry-after": "0"})] * 10)
    client = OpenRouterClient(
        api_key="k", base_url="http://x", transport=transport,
        retry_backoff=lambda a: 0.0, max_retries=5,
    )
    with pytest.raises(OpenRouterError):
        await client.chat(model="m", system="s", user="u", timeout=1.0, max_retries=1)
    assert counter["i"] == 1
