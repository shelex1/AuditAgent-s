from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

import httpx

from ..errors import OpenRouterError


DEFAULT_RETRY_SCHEDULE = [2.0, 5.0, 15.0]


@dataclass(frozen=True)
class OpenRouterResponse:
    text: str
    model: str


class OpenRouterClient:
    """Async OpenRouter client with bounded retries and classification of errors.

    Network/timeout/5xx/429 errors get retried with a backoff schedule. After
    the schedule is exhausted, an OpenRouterError is raised. The caller
    decides whether a single member abstains or the whole debate aborts.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
        retry_backoff: Callable[[int], float] | None = None,
        max_retries: int = 3,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._retry_backoff = retry_backoff or (
            lambda attempt: DEFAULT_RETRY_SCHEDULE[min(attempt, len(DEFAULT_RETRY_SCHEDULE) - 1)]
        )
        self._max_retries = max_retries

    async def chat(
        self,
        *,
        model: str,
        system: str,
        user: str,
        timeout: float,
        response_format_json: bool = True,
    ) -> OpenRouterResponse:
        payload: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if response_format_json:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/chat/completions"

        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                async with httpx.AsyncClient(
                    transport=self._transport, timeout=timeout
                ) as hc:
                    try:
                        r = await asyncio.wait_for(
                            hc.post(url, headers=headers, json=payload),
                            timeout=timeout,
                        )
                    except asyncio.TimeoutError:
                        raise httpx.TimeoutException(f"timed out after {timeout}s")

                if r.status_code == 200:
                    data = r.json()
                    content = data["choices"][0]["message"]["content"]
                    return OpenRouterResponse(text=content, model=model)
                if r.status_code == 429:
                    last_exc = OpenRouterError(f"rate limit (attempt {attempt + 1})")
                elif 500 <= r.status_code < 600:
                    last_exc = OpenRouterError(f"upstream {r.status_code} (attempt {attempt + 1})")
                else:
                    raise OpenRouterError(f"unexpected status {r.status_code}: {r.text[:200]}")
            except httpx.TimeoutException as exc:
                last_exc = OpenRouterError(f"timeout after {timeout}s (attempt {attempt + 1})")
            except httpx.HTTPError as exc:
                last_exc = OpenRouterError(f"network error: {exc}")
            except (KeyError, ValueError) as exc:
                raise OpenRouterError(f"malformed response: {exc}") from exc

            if attempt < self._max_retries - 1:
                await asyncio.sleep(self._retry_backoff(attempt))

        if last_exc is None:
            raise OpenRouterError("retry loop exited without an exception (unreachable)")
        raise last_exc
