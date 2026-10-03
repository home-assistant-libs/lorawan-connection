---
title: Installation
description: Install the shared package or run its source examples.
---

Use Python 3.12 or later.

```sh
pip install lorawan-connection
```

The package has no runtime dependencies. It does not install a network backend.
The application that supplies events owns that backend.

## Run from source

Use this path before the first PyPI publication, or to run the examples:

```sh
git clone https://github.com/home-assistant-libs/lorawan-connection.git
cd lorawan-connection
uv sync --group compatibility
uv run python examples/replay.py
```

The optional development group installs the pinned generated ChirpStack bindings
for compatibility tests. Device libraries do not need that group.

The wheel includes `py.typed`. Type checkers can check your collection, models,
and fixture payloads against the public contracts.

Continue with the [quickstart](/lorawan-connection/getting-started/quickstart/).
