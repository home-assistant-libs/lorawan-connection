---
title: Installation
description: Install lorawan-connection from PyPI.
---

Use Python 3.12 or later.

```sh
pip install lorawan-connection
```

The base installation has no runtime dependencies. The application owns the
connection that supplies events.
Backend modules live under `lorawan_connection.backend`. Importing the shared
package or the backend namespace does not load adapters or their dependencies.

The wheel includes `py.typed`. Type checkers can check your collection, models,
and fixture payloads against the public contracts.

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

Import `TTSConnection` from `lorawan_connection.backend.tts`. The CLI selects it
with `--backend tts`. Its gRPC and protobuf dependencies remain optional.
See [Connecting to The Things Stack](/lorawan-connection/connection/tts/).
