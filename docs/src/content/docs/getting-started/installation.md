---
title: Installation
description: Install lorawan-connection from PyPI.
---

Use Python 3.12 or later.

```sh
pip install lorawan-connection
```

The package has no runtime dependencies. It does not install a network backend.
The application that supplies events owns that backend.

The wheel includes `py.typed`. Type checkers can check your collection, models,
and fixture payloads against the public contracts.

Continue with the [quickstart](/lorawan-connection/getting-started/quickstart/).
