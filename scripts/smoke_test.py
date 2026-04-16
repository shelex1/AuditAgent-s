"""Smoke-test every configured model. NOT part of pytest; run manually.

Usage:
    python scripts/smoke_test.py [path/to/council.toml]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from anti_hacker.config import load_config
from anti_hacker.openrouter.client import OpenRouterClient


async def _probe(client: OpenRouterClient, model: str, timeout: int) -> tuple[str, bool, str]:
    try:
        resp = await client.chat(
            model=model,
            system="Reply with exactly the JSON {\"ok\": true}.",
            user="ping",
            timeout=timeout,
        )
        text = resp.text or ""
        ok = "ok" in text.lower()
        return model, ok, text[:80] if text else "<empty>"
    except Exception as exc:
        return model, False, str(exc)


async def main() -> int:
    toml_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config/council.toml")
    cfg = load_config(toml_path)
    client = OpenRouterClient(api_key=cfg.api_key, base_url=cfg.openrouter.base_url)

    targets = [(m.model, m.timeout) for m in cfg.members]
    targets.append((cfg.cartographer.model, cfg.cartographer.timeout))

    print(f"Probing {len(targets)} models...")
    results = await asyncio.gather(*[_probe(client, m, t) for m, t in targets])
    fails = 0
    for model, ok, sample in results:
        flag = "OK" if ok else "FAIL"
        print(f"[{flag}] {model} — {sample}")
        if not ok:
            fails += 1
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
