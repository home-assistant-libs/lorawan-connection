# The Things Stack API definitions

`tts_schema.pb` contains the official protobuf descriptors needed by the TTS
adapter, with wire-relevant options. HTTP, validation, and code-generation
annotations are removed. Standard protobuf types come from the optional protobuf
dependency. The descriptor set is generated from TheThingsNetwork/lorawan-stack
at commit
`41f1bf2a05de6c6335b9c320117bbb03cd780bec` (3.36.2).

Source: https://github.com/TheThingsNetwork/lorawan-stack/tree/41f1bf2a05de6c6335b9c320117bbb03cd780bec/api

The source is licensed under Apache 2.0; see `TTS_LICENSE`. The adapter loads the
definitions into a private descriptor pool; it does not install top-level `ttn` or `google` packages.

Regenerate with `uv run --project script/tts_spike python
script/generate_tts_schema.py ../lorawan-stack`. The script checks the source
revision and uses the spike environment's protobuf compiler. Runtime users need
only the `tts` extra, not a compiler or a source checkout.
