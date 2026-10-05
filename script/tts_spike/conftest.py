"""Load the locally generated TTS bindings for the external spike tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "generated"))
