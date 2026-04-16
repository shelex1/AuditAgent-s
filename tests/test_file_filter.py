from pathlib import Path

import pytest

from anti_hacker.scanners.file_filter import (
    is_binary_file,
    iter_project_files,
    path_is_under,
)


def test_is_binary_on_null_bytes(tmp_path: Path) -> None:
    f = tmp_path / "b.bin"
    f.write_bytes(b"\x00\x01text here")
    assert is_binary_file(f)


def test_is_binary_false_on_source(tmp_path: Path) -> None:
    f = tmp_path / "s.py"
    f.write_text("def f(): return 1\n", encoding="utf-8")
    assert not is_binary_file(f)


def test_iter_project_files_respects_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored/\n*.log\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "b.log").write_text("x", encoding="utf-8")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "c.py").write_text("x", encoding="utf-8")

    paths = {p.relative_to(tmp_path).as_posix() for p in iter_project_files(tmp_path)}
    assert "a.py" in paths
    assert "b.log" not in paths
    assert all("ignored/" not in p for p in paths)


def test_iter_project_files_skips_binaries(tmp_path: Path) -> None:
    (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    (tmp_path / "t.py").write_text("x", encoding="utf-8")
    paths = {p.name for p in iter_project_files(tmp_path)}
    assert "img.png" not in paths
    assert "t.py" in paths


def test_iter_project_files_skips_default_dirs(tmp_path: Path) -> None:
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "pkg.py").write_text("x", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("x", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("x", encoding="utf-8")
    (tmp_path / "ok.py").write_text("x", encoding="utf-8")

    paths = {p.relative_to(tmp_path).as_posix() for p in iter_project_files(tmp_path)}
    assert paths == {"ok.py"}


def test_path_is_under_accepts_subpaths(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.py").write_text("", encoding="utf-8")
    assert path_is_under(tmp_path / "sub" / "a.py", tmp_path)


def test_path_is_under_rejects_escape(tmp_path: Path) -> None:
    # ../../etc/passwd style
    bad = tmp_path.parent / "outside.txt"
    bad.write_text("", encoding="utf-8")
    try:
        assert not path_is_under(bad, tmp_path)
    finally:
        bad.unlink()
