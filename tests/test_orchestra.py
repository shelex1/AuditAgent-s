import asyncio
import json

import httpx
import pytest

from anti_hacker.config import MemberConfig
from anti_hacker.council.member import CouncilMember
from anti_hacker.council.orchestra import DebateOrchestra, RoundResult
from anti_hacker.openrouter.client import OpenRouterClient
from tests.fixtures.sample_responses import chat_completion


def _member(name: str, content_by_round: list[str]) -> CouncilMember:
    # each call returns the next canned response
    counter = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = counter["i"]
        counter["i"] += 1
        text = content_by_round[min(i, len(content_by_round) - 1)]
        return httpx.Response(200, json=chat_completion(text))

    client = OpenRouterClient(
        api_key="sk-test",
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(handler),
        retry_backoff=lambda a: 0,
    )
    cfg = MemberConfig(name=name, model=f"p/{name}:free", role="pragmatic-engineer", timeout=5)
    return CouncilMember(config=cfg, client=client)


ROUND1_OK = json.dumps({"findings": [], "confidence": 7, "reasoning": "clean"})
ROUND2_OK = json.dumps({"agree_with": [], "disagree_with": [], "missed_findings": [], "updated_confidence": 7})
ROUND3_OK = json.dumps({"final_findings": [], "unified_patch": "", "final_confidence": 7})


@pytest.mark.asyncio
async def test_happy_path_all_members_respond(tmp_path) -> None:
    members = [_member(f"m{i}", [ROUND1_OK, ROUND2_OK, ROUND3_OK]) for i in range(5)]
    orch = DebateOrchestra(members=members, debate_timeout=30, max_bytes_per_file=1024)
    result = await orch.run(task="t", files={"a.py": "x"}, mode="review")
    assert len(result.round1) == 5
    assert len(result.round2) == 5
    assert len(result.round3) == 5
    assert result.abstained == []


@pytest.mark.asyncio
async def test_one_member_malformed_json_abstains(tmp_path) -> None:
    bad_round1 = "not json at all"
    members = [
        _member("m0", [bad_round1, bad_round1, bad_round1]),  # unrepair-able
    ] + [_member(f"m{i}", [ROUND1_OK, ROUND2_OK, ROUND3_OK]) for i in range(1, 5)]
    orch = DebateOrchestra(members=members, debate_timeout=30, max_bytes_per_file=1024)
    result = await orch.run(task="t", files={"a.py": "x"}, mode="review")
    assert "m0" in result.abstained
    assert len(result.round1) == 4  # the other 4 proceeded


@pytest.mark.asyncio
async def test_global_timeout_cancels_in_flight() -> None:
    # Simulate a very slow member by giving it a tiny per-member timeout below the orchestra's tight budget
    slow_members = []
    for i in range(5):
        async def slow_handler(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(1.0)
            return httpx.Response(200, json=chat_completion(ROUND1_OK))
        client = OpenRouterClient(
            api_key="sk-test",
            base_url="https://openrouter.ai/api/v1",
            transport=httpx.MockTransport(slow_handler),
            retry_backoff=lambda a: 0,
            max_retries=1,
        )
        cfg = MemberConfig(name=f"slow{i}", model="x", role="pragmatic-engineer", timeout=5)
        slow_members.append(CouncilMember(config=cfg, client=client))

    orch = DebateOrchestra(members=slow_members, debate_timeout=0.1, max_bytes_per_file=1024)
    result = await orch.run(task="t", files={"a.py": "x"}, mode="review")
    assert result.partial_timeout is True
