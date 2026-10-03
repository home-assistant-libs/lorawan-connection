"""Build a release version in an isolated copy of the source tree."""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    if (
        len(sys.argv) != 2
        or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", sys.argv[1]) is None
    ):
        raise SystemExit("Usage: python script/build_release.py MAJOR.MINOR.PATCH")
    version = sys.argv[1]
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="lorawan-release-") as temporary:
        source = Path(temporary) / "source"
        shutil.copytree(
            root,
            source,
            ignore=shutil.ignore_patterns(
                ".git",
                ".venv",
                "node_modules",
                "dist",
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                ".ruff_cache",
                ".astro",
                ".coverage",
                "uv.lock",
            ),
        )
        project = source / "pyproject.toml"
        original = project.read_text()
        updated, count = re.subn(
            r'^version = "0\.0\.0"$',
            f'version = "{version}"',
            original,
            flags=re.MULTILINE,
        )
        if count != 1:
            raise SystemExit("Expected exactly one source version = 0.0.0")
        project.write_text(updated)
        subprocess.run(
            ["uv", "build", str(source), "--out-dir", str(root / "dist")], check=True
        )


if __name__ == "__main__":
    main()
