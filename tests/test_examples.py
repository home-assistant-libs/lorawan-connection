"""Keep the source embedded by the documentation executable."""

import runpy
from pathlib import Path

import pytest


def test_documented_replay(capsys: pytest.CaptureFixture[str]) -> None:
    runpy.run_path(
        str(Path(__file__).parents[1] / "examples/replay.py"), run_name="__main__"
    )
    output = capsys.readouterr().out
    assert "Device: Greenhouse" in output
    assert "temperature=21.4, humidity=31.4" in output
