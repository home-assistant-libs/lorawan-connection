---
title: Installation
description: Install lorawan-connection from PyPI.
---

Use Python 3.12 or later.

```sh
pip install lorawan-connection
```

The base package has no runtime dependencies and includes `py.typed` for type checking.
Backend modules live under `lorawan_connection.backend` and load only when imported
explicitly.

Continue with the [quickstart](/lorawan-connection/getting-started/quickstart/).

## Optional ChirpStack backend

```sh
pip install "lorawan-connection[chirpstack]"
```

Use this extra for the [ChirpStack backend](/lorawan-connection/connection/chirpstack/) or a
device library’s [CLI](/lorawan-connection/patterns/cli/).

Import the adapter explicitly:

```python
from lorawan_connection.backend.chirpstack import ChirpStackConnection
```

The CLI selects it with `--backend chirpstack` and imports it only when connecting.

## Optional The Things Stack backend

```sh
pip install "lorawan-connection[tts]"
```

Import `TTSConnection` from `lorawan_connection.backend.tts`, or select
`--backend tts` in the CLI. See
[Connecting to The Things Stack](/lorawan-connection/connection/tts/).
