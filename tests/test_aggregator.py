from anti_hacker.council.aggregator import (
    aggregate,
    AggregatedResult,
    parse_member_json,
    similarity,
)


def _round3(findings: list[dict], patch: str, confidence: int = 8) -> dict:
    return {"final_findings": findings, "unified_patch": patch, "final_confidence": confidence}


def test_three_of_five_agree_is_consensus() -> None:
    members = {
        "m1": _round3([{"line": 10, "severity": "high", "description": "SQL injection"}], "PATCH_A"),
        "m2": _round3([{"line": 10, "severity": "high", "description": "SQL injection"}], "PATCH_A"),
        "m3": _round3([{"line": 10, "severity": "critical", "description": "SQL injection"}], "PATCH_A"),
        "m4": _round3([], ""),
        "m5": _round3([], ""),
    }
    result = aggregate(round3=members, total_members=5)
    assert result.verdict == "FOUND"
    assert len(result.findings) == 1
    assert result.findings[0]["line"] == 10
    assert result.findings[0]["severity"] == "high"  # median of high,high,critical -> high
    assert result.winning_patch == "PATCH_A"
    assert result.confidence == "3/5 models agree"


def test_two_two_one_split_no_consensus() -> None:
    members = {
        "m1": _round3([{"line": 1, "severity": "high", "description": "A"}], ""),
        "m2": _round3([{"line": 1, "severity": "high", "description": "A"}], ""),
        "m3": _round3([{"line": 5, "severity": "high", "description": "B"}], ""),
        "m4": _round3([{"line": 5, "severity": "high", "description": "B"}], ""),
        "m5": _round3([], ""),
    }
    result = aggregate(round3=members, total_members=5)
    assert result.verdict == "SPLIT"
    # no finding reaches 3/5
    assert result.findings == []


def test_no_findings_all_agree() -> None:
    members = {f"m{i}": _round3([], "") for i in range(1, 6)}
    result = aggregate(round3=members, total_members=5)
    assert result.verdict == "CLEAN"


def test_abstainers_reduce_quorum_base() -> None:
    # only 3 members active (2 abstained by being absent from dict)
    members = {
        "m1": _round3([{"line": 1, "severity": "high", "description": "X"}], ""),
        "m2": _round3([{"line": 1, "severity": "high", "description": "X"}], ""),
        "m3": _round3([{"line": 1, "severity": "high", "description": "X"}], ""),
    }
    result = aggregate(round3=members, total_members=5)
    # all 3 active agree -> consensus
    assert result.verdict == "FOUND"
    assert result.confidence == "3/3 models agree"
    assert result.abstained_count == 2


def test_below_quorum_returns_quorum_lost() -> None:
    members = {
        "m1": _round3([{"line": 1, "severity": "high", "description": "X"}], ""),
        "m2": _round3([{"line": 1, "severity": "high", "description": "X"}], ""),
    }
    result = aggregate(round3=members, total_members=5)
    assert result.verdict == "QUORUM_LOST"


def test_identical_patches_group_together() -> None:
    members = {
        "m1": _round3([], "--- a/x\n+++ b/x\n@@\n-a\n+b\n"),
        "m2": _round3([], "--- a/x\n+++ b/x\n@@\n-a\n+b\n"),
        "m3": _round3([], "--- a/x\n+++ b/x\n@@\n-a\n+b\n"),
        "m4": _round3([], "different"),
        "m5": _round3([], ""),
    }
    result = aggregate(round3=members, total_members=5)
    assert result.winning_patch == "--- a/x\n+++ b/x\n@@\n-a\n+b\n"
    assert len(result.alternative_patches) == 1  # "different"


def test_parse_valid_member_json() -> None:
    raw = '{"final_findings": [], "unified_patch": "", "final_confidence": 7}'
    ok, payload, err = parse_member_json(raw)
    assert ok
    assert payload["final_confidence"] == 7


def test_parse_invalid_member_json() -> None:
    ok, payload, err = parse_member_json("not json at all")
    assert not ok
    assert err  # non-empty


def test_similarity_ignores_whitespace() -> None:
    a = "--- a/x\n+++ b/x\n@@\n-foo\n+bar\n"
    b = "--- a/x\n+++ b/x\n@@\n-foo\n+bar\n\n\n"
    assert similarity(a, b) > 0.9
