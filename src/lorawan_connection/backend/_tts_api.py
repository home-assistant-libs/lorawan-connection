"""Private gRPC bindings from the bundled official TTS descriptor set."""

from importlib import import_module
from importlib.resources import files
from typing import Any

import grpc
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

_POOL = descriptor_pool.DescriptorPool()
for _file in descriptor_pb2.FileDescriptorSet.FromString(
    files(__package__).joinpath("tts_schema.pb").read_bytes()
).file:
    for _dependency in _file.dependency:
        if _dependency.startswith("google/protobuf/"):
            _module = import_module(
                _dependency.removesuffix(".proto").replace("/", ".") + "_pb2"
            )
            _POOL.AddSerializedFile(_module.DESCRIPTOR.serialized_pb)
    _POOL.Add(_file)


def message(name: str, /, **fields: Any) -> Any:
    """Construct an official ttn.lorawan.v3 message without global registrations."""
    return message_factory.GetMessageClass(
        _POOL.FindMessageTypeByName(f"ttn.lorawan.v3.{name}")
    )(**fields)


class Service:
    """Bind only the methods declared by an official gRPC service."""

    def __init__(self, channel: grpc.aio.Channel, name: str) -> None:
        service = _POOL.FindServiceByName(f"ttn.lorawan.v3.{name}")
        for method in service.methods:
            request = message_factory.GetMessageClass(method.input_type)
            response = message_factory.GetMessageClass(method.output_type)
            bind = (
                channel.unary_stream if method.server_streaming else channel.unary_unary
            )
            setattr(
                self,
                method.name,
                bind(
                    f"/{service.full_name}/{method.name}",
                    request_serializer=request.SerializeToString,
                    response_deserializer=response.FromString,
                ),
            )

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)
