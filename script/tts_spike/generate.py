"""Generate Python bindings from an official lorawan-stack checkout."""

import argparse
import subprocess
import sys
from pathlib import Path

import grpc_tools

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("repository", type=Path)
args = parser.parse_args()
api = args.repository.resolve() / "api"
output = Path(__file__).parent / "generated"
output.mkdir(exist_ok=True)
files = [str(path.relative_to(api)) for path in api.glob("ttn/**/*.proto")]
files.extend(
    str(path.relative_to(api / "third_party"))
    for path in (api / "third_party").rglob("*.proto")
)
subprocess.run(
    [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"-I{api}/third_party",
        f"-I{api}",
        f"-I{Path(grpc_tools.__file__).parent}/_proto",
        f"--python_out={output}",
        f"--grpc_python_out={output}",
        *files,
    ],
    check=True,
)
print(f"Generated bindings from {len(files)} official protobuf definitions")
