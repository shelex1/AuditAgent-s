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
        base_payload: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/chat/completions"

        last_exc: Exception | None = None
        use_json_mode = response_format_json  # may flip to False on 400
        attempt = 0
        while attempt < self._max_retries:
            sleep_override: float | None = None
            payload = dict(base_payload)
            if use_json_mode:
                payload["response_format"] = {"type": "json_object"}
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
                    choices = data.get("choices") or []
                    if not choices:
                        raise OpenRouterError("malformed response: empty choices")
                    content = choices[0]["message"]["content"]
                    return OpenRouterResponse(text=content, model=model)
                if r.status_code == 400 and use_json_mode:
                    # Some free models reject response_format; retry without it
                    # on the same attempt (does not count toward max_retries).
                    use_json_mode = False
                    continue  # no backoff, attempt count unchanged
                if r.status_code == 429:
                    retry_after = r.headers.get("retry-after")
                    if retry_after is not None:
                        try:
                            sleep_override = float(retry_after)
                        except (TypeError, ValueError):
                            sleep_override = None
                    last_exc = OpenRouterError(f"rate limit (attempt {attempt + 1})")
                elif 500 <= r.status_code < 600:
                    last_exc = OpenRouterError(f"upstream {r.status_code} (attempt {attempt + 1})")
                else:
                    raise OpenRouterError(f"unexpected status {r.status_code}: {r.text[:200]}")
            except httpx.TimeoutException:
                last_exc = OpenRouterError(f"timeout after {timeout}s (attempt {attempt + 1})")
            except httpx.HTTPError as exc:
                last_exc = OpenRouterError(f"network error: {exc}")
            except (KeyError, ValueError, IndexError) as exc:
                raise OpenRouterError(f"malformed response: {exc}") from exc

            if attempt < self._max_retries - 1:
                delay = sleep_override if sleep_override is not None else self._retry_backoff(attempt)
                await asyncio.sleep(delay)
            attempt += 1

        if last_exc is None:
            raise OpenRouterError("retry loop exited without an exception (unreachable)")
        raise last_exc
