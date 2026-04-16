from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Literal


Verdict = Literal["FOUND", "CLEAN", "SPLIT", "QUORUM_LOST"]

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
SEVERITY_BY_ORDER = {v: k for k, v in SEVERITY_ORDER.items()}


@dataclass
class AggregatedResult:
    verdict: Verdict
    findings: list[dict[str, Any]] = field(default_factory=list)
    winning_patch: str = ""
    alternative_patches: list[str] = field(default_factory=list)
    confidence: str = ""
    abstained_count: int = 0
    per_finding_support: list[dict[str, Any]] = field(default_factory=list)


def parse_member_json(raw: str) -> tuple[bool, dict[str, Any], str]:
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return False, {}, "JSON is not an object"
        return True, data, ""
    except json.JSONDecodeError as exc:
        return False, {}, str(exc)


def _normalize_patch(p: str) -> str:
    """Remove trailing whitespace per line + collapse trailing blanks."""
    lines = [ln.rstrip() for ln in p.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize_patch(a), _normalize_patch(b)).ratio()


def _group_patches(patches: list[tuple[str, str]], threshold: float = 0.9) -> list[list[tuple[str, str]]]:
    """patches = [(member_name, patch_text)]. Returns groups."""
    groups: list[list[tuple[str, str]]] = []
    for member, patch in patches:
        placed = False
        for g in groups:
            if similarity(g[0][1], patch) >= threshold:
                g.append((member, patch))
                placed = True
                break
        if not placed:
            groups.append([(member, patch)])
    return groups


def _finding_key(f: dict[str, Any]) -> tuple[int, str]:
    line = int(f.get("line", 0) or 0)
    desc = str(f.get("description", "")).strip().lower()
    # normalize by grabbing first few meaningful tokens
    tokens = re.findall(r"[a-z0-9]+", desc)[:6]
    return (line, " ".join(tokens))


def _median_severity(severities: list[str]) -> str:
    vals = [SEVERITY_ORDER.get(s, 1) for s in severities]
    m = int(statistics.median(vals))
    return SEVERITY_BY_ORDER[m]


def aggregate(*, round3: dict[str, dict[str, Any]], total_members: int) -> AggregatedResult:
    active = len(round3)
    abstained = total_members - active

    if active < 3:
        return AggregatedResult(verdict="QUORUM_LOST", abstained_count=abstained)

    # cluster findings
    key_to_members: dict[tuple[int, str], list[str]] = {}
    key_to_examples: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for member, payload in round3.items():
        for f in payload.get("final_findings", []) or []:
            k = _finding_key(f)
            key_to_members.setdefault(k, []).append(member)
            key_to_examples.setdefault(k, []).append(f)

    threshold = max(3, (active // 2) + 1)  # simple majority, min 3
    accepted: list[dict[str, Any]] = []
    per_finding_support: list[dict[str, Any]] = []
    any_finding_produced = bool(key_to_members)

    for k, members in key_to_members.items():
        supporting = sorted(set(members))
        dissent = sorted(set(round3.keys()) - set(members))
        examples = key_to_examples[k]
        if len(supporting) >= threshold:
            severities = [str(f.get("severity", "medium")) for f in examples]
            accepted.append(
                {
                    "line": examples[0].get("line", 0),
                    "severity": _median_severity(severities),
                    "description": examples[0].get("description", ""),
                    "supporting_models": supporting,
                    "dissenting_models": dissent,
                }
            )
        per_finding_support.append(
            {
                "line": examples[0].get("line", 0),
                "description": examples[0].get("description", ""),
                "supporting_models": supporting,
                "dissenting_models": dissent,
                "accepted": len(supporting) >= threshold,
            }
        )

    # patch selection
    patches = [
        (member, payload.get("unified_patch", "") or "")
        for member, payload in round3.items()
        if (payload.get("unified_patch") or "").strip()
    ]
    winning_patch = ""
    alternatives: list[str] = []
    if patches:
        groups = _group_patches(patches)
        groups.sort(key=len, reverse=True)
        winning_patch = groups[0][0][1]
        # keep one exemplar per other group
        alternatives = [g[0][1] for g in groups[1:]]

    # verdict
    if accepted:
        verdict: Verdict = "FOUND"
    elif not any_finding_produced:
        verdict = "CLEAN"
    else:
        verdict = "SPLIT"

    confidence = ""
    if verdict == "FOUND":
        best_support = max(len(f["supporting_models"]) for f in accepted)
        confidence = f"{best_support}/{active} models agree"

    return AggregatedResult(
        verdict=verdict,
        findings=accepted,
        winning_patch=winning_patch,
        alternative_patches=alternatives,
        confidence=confidence,
        abstained_count=abstained,
        per_finding_support=per_finding_support,
    )
