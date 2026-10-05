"""Build a private descriptor set from the pinned official TTS API checkout."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import grpc_tools
from google.protobuf import descriptor_pb2

REVISION = "41f1bf2a05de6c6335b9c320117bbb03cd780bec"


def strip_options(value) -> None:
    """Keep wire-relevant options and remove imports used only for annotations."""
    for field, child in value.ListFields():
        if field.name == "options":
            saved = {
                name: getattr(child, name)
                for name in ("map_entry", "packed", "allow_alias")
                if name in child.DESCRIPTOR.fields_by_name and child.HasField(name)
            }
            child.Clear()
            for name, setting in saved.items():
                setattr(child, name, setting)
        elif field.message_type is not None:
            for item in child if field.is_repeated else [child]:
                strip_options(item)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    args = parser.parse_args()
    repository = args.repository.resolve()
    revision = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != REVISION:
        parser.error(f"Expected lorawan-stack revision {REVISION}")
    api = repository / "api"
    output = Path(__file__).parents[1] / "src/lorawan_connection/backend/tts_schema.pb"
    with tempfile.TemporaryDirectory() as temporary:
        descriptor = Path(temporary) / "schema.pb"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "grpc_tools.protoc",
                f"-I{api}/third_party",
                f"-I{api}",
                f"-I{Path(grpc_tools.__file__).parent}/_proto",
                f"--descriptor_set_out={descriptor}",
                "--include_imports",
                "ttn/lorawan/v3/application_services.proto",
                "ttn/lorawan/v3/applicationserver.proto",
                "ttn/lorawan/v3/end_device_services.proto",
                "ttn/lorawan/v3/events.proto",
            ],
            check=True,
        )
        schema = descriptor_pb2.FileDescriptorSet.FromString(descriptor.read_bytes())
        # Runtime needs wire definitions, not upstream HTTP/validation/codegen options.
        kept = descriptor_pb2.FileDescriptorSet()
        for definition in schema.file:
            if not definition.name.startswith("ttn/"):
                continue
            strip_options(definition)
            dependencies = [
                name
                for name in definition.dependency
                if name.startswith(("ttn/", "google/protobuf/"))
            ]
            del definition.dependency[:]
            definition.dependency.extend(dependencies)
            del definition.public_dependency[:]
            del definition.weak_dependency[:]
            kept.file.add().CopyFrom(definition)
        output.write_bytes(kept.SerializeToString(deterministic=True))
    print(f"Wrote {output} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
