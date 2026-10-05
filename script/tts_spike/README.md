# The Things Stack external test harness

This external test harness exercises `lorawan_connection.backend.tts` from the
installed library. It uses the actual TTS gRPC services with generated Python bindings. It does
not use MQTT or HTTP callbacks.

The tested server is TTS 3.36.2. Bindings were generated from
`TheThingsNetwork/lorawan-stack` commit
`41f1bf2a05de6c6335b9c320117bbb03cd780bec`.

## Run

Use a disposable local server listening on `127.0.0.1:18849`. The test creates its
own application, devices, session keys and application API keys. It requires a
local user named `spike-admin` and a file containing that user's API key. It never
prints the API key. The user key is used only to prepare the test; the adapter
receives a restricted application key.

From the library checkout:

```sh
uv sync --project script/tts_spike
uv run --project script/tts_spike python script/tts_spike/generate.py ../lorawan-stack
uv run --all-extras --group compatibility pytest tests/test_tts.py
uv run --project script/tts_spike python script/tts_spike/live.py \
  --admin-key-file /path/to/local-test-admin-key.txt
```

Generated protobuf modules stay in the ignored `generated/` directory. Their
source definitions carry the upstream license. The spike does not bundle these
modules into the library.

`stack.yml` disables TLS and MQTT and binds gRPC and HTTP to loopback. Use it only
for this local test. Start a separate PostgreSQL instance on port 15439 with a
UTF-8 database named `tts_spike`, and Redis on port 16389. Set the database URI for
your local user, then run the official server binary:

```sh
export TTN_LW_IS_DATABASE_URI="postgres://$(whoami)@127.0.0.1:15439/tts_spike?sslmode=disable"
ttn-lw-stack -c script/tts_spike/stack.yml is-db migrate
ttn-lw-stack -c script/tts_spike/stack.yml is-db create-admin-user \
  --id spike-admin --email spike@example.test --password local-test-only
ttn-lw-stack -c script/tts_spike/stack.yml is-db create-user-api-key \
  --user-id spike-admin > /path/to/local-test-admin-key.txt
ttn-lw-stack -c script/tts_spike/stack.yml start is ns as js
```

Stop the test server and its database and Redis instances when finished. Do not
reuse the generated session keys or the fixed cluster key outside this test.

## What the live test covers

- List devices and select the S2101 model through its TTS brand/model identity.
- Stream simulated uplinks through `AppAs.Subscribe` and decode raw bytes into
  21.4 °C and 31.4% humidity using the existing SenseCAP model.
- Queue a confirmed downlink and retain a caller-generated correlation ID.
- Simulate a device ACK, receive it over the stream, and complete the model's
  `async_send_downlink()` call.
- Read device registrations with a read-only application key and reject writes.
- Discover device additions, renames and removals through `Events.Stream`.

The server, APIs and streams are real. Radio reception and acknowledgements are
simulated; this does not test a physical device or TTN Community Edition hosting.

## Packaged backend

The adapter is in `src/lorawan_connection/backend/tts.py`. Its `tts` extra supplies
gRPC and protobuf. A private descriptor set is packaged with the wheel; runtime
users do not generate bindings. `script/generate_tts_schema.py` reproduces that set
from the pinned upstream checkout. This harness generates additional bindings
only for provisioning the disposable test environment.

The adapter supports separate Identity and Application Server endpoints. It
reconciles startup inventory, checks read permissions, watches lifecycle events,
and polls inventory as a fallback. The HA `the_things_stack` integration owns
recovery and reauthentication. Its external test is
`script/lorawan_poc/real_tts.py` in the Core worktree.

TTS does not support queue expiry. The backend rejects `expires_at`; Dragino's
expiry-requiring relay methods remain unsupported through TTS. The harness tests
raw commands without expiry. Devices without a DevEUI are skipped.

The generic connection protocol uses `(stack, brand_id)` filters. Device models
keep each stack's native catalog IDs. Collections need no server-selection logic.
