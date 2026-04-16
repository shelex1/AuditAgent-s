import json
from pathlib import Path

import pytest

from anti_hacker.io.debate_log import DebateLog, load_debate_log


def test_write_read_round_trip(tmp_path: Path) -> None:
    log = DebateLog(debate_id="d1", root=tmp_path)
    log.record_round(1, {"m1": {"findings": []}})
    log.record_round(2, {"m1": {"agree_with": []}})
    log.finalize({"verdict": "ok"})

    loaded = load_debate_log("d1", root=tmp_path)
    assert loaded["debate_id"] == "d1"
    assert loaded["rounds"][0]["round"] == 1
    assert loaded["final"] == {"verdict": "ok"}


def test_write_is_atomic_no_partial_file(tmp_path: Path, monkeypatch) -> None:
    log = DebateLog(debate_id="d2", root=tmp_path)
    log.record_round(1, {"m1": {}})

    # Simulate crash mid-finalize by patching rename
    def boom(src, dst):
        raise OSError("disk full")

    import os as _os
    monkeypatch.setattr(_os, "replace", boom)

    with pytest.raises(OSError):
        log.finalize({"verdict": "x"})

    # The final file must NOT exist (no partial write visible)
    target = tmp_path / "debates" / "d2.json"
    assert not target.exists()


def test_load_missing_debate_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_debate_log("nonexistent", root=tmp_path)
