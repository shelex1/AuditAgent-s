from anti_hacker.tools.thinking import ThinkingService


def _basic(**overrides):
    base = {
        "thought": "first step",
        "thought_number": 1,
        "total_thoughts": 3,
        "next_thought_needed": True,
    }
    base.update(overrides)
    return base


def test_basic_thought_appends_to_history() -> None:
    svc = ThinkingService()
    result = svc.process_thought(**_basic())
    assert result["thought_number"] == 1
    assert result["total_thoughts"] == 3
    assert result["next_thought_needed"] is True
    assert result["thought_history_length"] == 1
    assert result["branches"] == []


def test_optional_fields_accepted() -> None:
    svc = ThinkingService()
    result = svc.process_thought(
        thought="revising",
        thought_number=2,
        total_thoughts=3,
        next_thought_needed=True,
        is_revision=True,
        revises_thought=1,
        needs_more_thoughts=False,
    )
    assert result["thought_history_length"] == 1


def test_three_thoughts_grow_history() -> None:
    svc = ThinkingService()
    svc.process_thought(**_basic(thought="a", thought_number=1))
    svc.process_thought(**_basic(thought="b", thought_number=2))
    final = svc.process_thought(**_basic(thought="c", thought_number=3, next_thought_needed=False))
    assert final["thought_history_length"] == 3
    assert final["next_thought_needed"] is False


def test_auto_bump_total_thoughts() -> None:
    svc = ThinkingService()
    result = svc.process_thought(**_basic(thought_number=5, total_thoughts=3))
    assert result["total_thoughts"] == 5


def test_two_branches_tracked_independently() -> None:
    svc = ThinkingService()
    svc.process_thought(**_basic())
    svc.process_thought(**_basic(
        thought="branch-a", thought_number=2, branch_from_thought=1, branch_id="a",
    ))
    svc.process_thought(**_basic(
        thought="branch-b", thought_number=2, branch_from_thought=1, branch_id="b",
        next_thought_needed=False,
    ))
    snap = svc.get_history()
    assert set(snap["branches"].keys()) == {"a", "b"}
    assert snap["total"] == 3


def test_multiple_thoughts_in_same_branch() -> None:
    svc = ThinkingService()
    svc.process_thought(**_basic(
        thought="t1", thought_number=1, branch_from_thought=1, branch_id="a",
    ))
    svc.process_thought(**_basic(
        thought="t2", thought_number=2, branch_from_thought=1, branch_id="a",
        next_thought_needed=False,
    ))
    snap = svc.get_history()
    assert list(snap["branches"].keys()) == ["a"]
    assert len(snap["branches"]["a"]) == 2


def test_get_history_full_snapshot() -> None:
    svc = ThinkingService()
    svc.process_thought(**_basic(thought="a", thought_number=1))
    svc.process_thought(**_basic(thought="b", thought_number=2))
    svc.process_thought(**_basic(thought="c", thought_number=3))
    svc.process_thought(**_basic(
        thought="br", thought_number=2, branch_from_thought=1, branch_id="x",
    ))
    snap = svc.get_history()
    assert snap["total"] == 4
    assert len(snap["history"]) == 4
    assert list(snap["branches"].keys()) == ["x"]


def test_get_history_filtered_by_branch() -> None:
    svc = ThinkingService()
    svc.process_thought(**_basic(thought="main", thought_number=1))
    svc.process_thought(**_basic(
        thought="br", thought_number=2, branch_from_thought=1, branch_id="x",
    ))
    snap = svc.get_history(branch_id="x")
    assert "branches" not in snap
    assert snap["total"] == 1
    assert snap["history"][0]["branch_id"] == "x"


def test_get_history_unknown_branch_returns_empty() -> None:
    svc = ThinkingService()
    svc.process_thought(**_basic())
    snap = svc.get_history(branch_id="nope")
    assert snap == {"history": [], "total": 0}


def test_long_thought_accepted() -> None:
    svc = ThinkingService()
    result = svc.process_thought(**_basic(thought="a" * 10_000))
    assert result["thought_history_length"] == 1
