from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Iterator


DEFAULT_EXCLUDE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", ".pytest_cache"}


def is_binary_file(path: Path, sample_size: int = 8192) -> bool:
    try:
        chunk = path.open("rb").read(sample_size)
    except OSError:
        return True
    return b"\x00" in chunk


def _load_gitignore_patterns(root: Path) -> list[str]:
    gi = root / ".gitignore"
    if not gi.exists():
        return []
    out = []
    for line in gi.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def _matches_gitignore(rel_posix: str, patterns: list[str]) -> bool:
    for pat in patterns:
        # directory marker
        if pat.endswith("/"):
            if rel_posix.startswith(pat) or f"/{pat[:-1]}/" in f"/{rel_posix}/":
                return True
        else:
            if fnmatch.fnmatch(rel_posix, pat):
                return True
            # also match a leading directory component
            if "/" not in pat and any(fnmatch.fnmatch(part, pat) for part in rel_posix.split("/")):
                return True
    return False


def iter_project_files(root: Path, *, max_bytes: int | None = None) -> Iterator[Path]:
    root = root.resolve()
    patterns = _load_gitignore_patterns(root)

    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(root).parts
        if any(part in DEFAULT_EXCLUDE_DIRS for part in rel_parts):
            continue
        rel = "/".join(rel_parts)
        if _matches_gitignore(rel, patterns):
            continue
        if is_binary_file(p):
            continue
        if max_bytes is not None:
            try:
                if p.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
        yield p


def path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
