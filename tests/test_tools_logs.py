from pathlib import Path

from anti_hacker.io.debate_log import DebateLog
from anti_hacker.io.proposals import ProposalStore
from anti_hacker.tools.logs import LogService


def test_get_debate_log_reads_from_disk(tmp_path: Path) -> None:
    log = DebateLog(debate_id="d42", root=tmp_path)
    log.record_round(1, {"m1": {"findings": []}})
    log.finalize({"verdict": "CLEAN"})
    svc = LogService(data_root=tmp_path)
    got = svc.get_debate_log("d42")
    assert got["debate_id"] == "d42"


def test_list_proposals_returns_patches(tmp_path: Path) -> None:
    store = ProposalStore(root=tmp_path)
    store.save(debate_id="d1", unified_diff="--- a/x\n+++ b/x\n", metadata={"summary": "a"})
    store.save(debate_id="d2", unified_diff="--- a/y\n+++ b/y\n", metadata={"summary": "b"})
    svc = LogService(data_root=tmp_path)
    listed = svc.list_proposals()
    ids = {p["debate_id"] for p in listed}
    assert ids == {"d1", "d2"}
