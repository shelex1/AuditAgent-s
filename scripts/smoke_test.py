"""Smoke-test every configured model. NOT part of pytest; run manually.

Usage:
    python scripts/smoke_test.py [path/to/council.toml] [--include-modal]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from anti_hacker.config import load_config, provider_by_name
from anti_hacker.openrouter.client import OpenRouterClient

_MODAL_PROBE_MODEL = "zai-org/GLM-5.1-FP8"


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


async def _probe_modal(cfg, toml_path: Path) -> int:
    """Single direct probe against the modal provider. Returns 0 on success, 1 on failure."""
    try:
        p = provider_by_name(cfg, "modal")
    except Exception as exc:
        print(f"[MODAL] ERROR — provider not found: {exc}")
        return 1
    client = OpenRouterClient(api_key=p.api_key, base_url=p.base_url)
    try:
        resp = await client.chat(
            model=_MODAL_PROBE_MODEL,
            system="You are a terse assistant.",
            user="Say 'ok'.",
            timeout=60.0,
            max_retries=1,
        )
        text = resp.text or ""
        print(f"[MODAL] provider=modal  model={_MODAL_PROBE_MODEL}  response={text[:80]!r}")
        return 0
    except Exception as exc:
        print(f"[MODAL] ERROR — {exc}")
        return 1


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test every configured model.",
    )
    parser.add_argument(
        "toml_path",
        nargs="?",
        default="config/council.toml",
        help="Path to council.toml (default: config/council.toml)",
    )
    parser.add_argument(
        "--include-modal",
        action="store_true",
        default=False,
        help="Also probe the modal provider directly (not part of the default run).",
    )
    args = parser.parse_args()

    toml_path = Path(args.toml_path)
    cfg = load_config(toml_path)
    primary = cfg.providers[0]
    client = OpenRouterClient(api_key=primary.api_key, base_url=primary.base_url)

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

    if args.include_modal:
        print()
        modal_rc = await _probe_modal(cfg, toml_path)
        if modal_rc != 0:
            fails += 1

    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
