"""Keep the shared package and CLI usable without any backend extras."""

import subprocess
import sys
from pathlib import Path


def test_imports_and_cli_without_backend_dependencies() -> None:
    """Block optional dependencies in a fresh interpreter, including CLI startup."""
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            """
import importlib
import importlib.abc
from pathlib import Path
import sys

sys.path[:0] = sys.argv[1:]

class NoExtras(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'grpc', 'chirpstack_api', 'google'}:
            raise ModuleNotFoundError(fullname)

sys.meta_path.insert(0, NoExtras())
import lorawan_connection
import lorawan_connection.backend

for path in Path(lorawan_connection.__file__).parent.glob('*.py'):
    importlib.import_module(f'lorawan_connection.{path.stem}')

from lorawan_connection.cli_helper import run
from sensecap_lorawan import SenseCapDeviceCollection

assert 'lorawan_connection.backend.chirpstack' not in sys.modules
assert 'lorawan_connection.backend.tts' not in sys.modules
try:
    run(SenseCapDeviceCollection.DEVICES, ['--help'])
except SystemExit as error:
    assert error.code == 0
else:
    raise AssertionError('Expected help to exit')
assert 'lorawan_connection.backend.chirpstack' not in sys.modules
assert 'lorawan_connection.backend.tts' not in sys.modules

try:
    run(SenseCapDeviceCollection.DEVICES,
        ['--backend', 'chirpstack', '--server', 'http://localhost:8080'])
except SystemExit as error:
    assert error.code == 1
else:
    raise AssertionError('Expected the missing extra to be reported')
try:
    run(SenseCapDeviceCollection.DEVICES,
        ['--backend', 'tts', '--application', 'app', '--server', 'http://localhost:1884'])
except SystemExit as error:
    assert error.code == 1
else:
    raise AssertionError('Expected the missing TTS extra to be reported')
""",
            str(root / "src"),
            str(root / "examples"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--backend" in result.stdout
    assert 'pip install "lorawan-connection[chirpstack]"' in result.stderr
    assert 'pip install "lorawan-connection[tts]"' in result.stderr
