import pytest
from pathlib import Path


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """A throwaway project directory for IO-heavy tests."""
    (tmp_path / "debates").mkdir()
    (tmp_path / "council_proposals").mkdir()
    (tmp_path / "logs").mkdir()
    return tmp_path
