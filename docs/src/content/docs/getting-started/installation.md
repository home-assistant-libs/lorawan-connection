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

The wheel includes `py.typed`. Type checkers can check your collection, models,
and fixture payloads against the public contracts.

Continue with the [quickstart](/lorawan-connection/getting-started/quickstart/).

## Optional ChirpStack backend

```sh
pip install "lorawan-connection[chirpstack]"
```

Use this extra for the [ChirpStack backend](/lorawan-connection/connection/chirpstack/) or a
device library’s [CLI](/lorawan-connection/patterns/cli/).
