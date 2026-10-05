# The Things Stack connection spike

This is an external test harness, separate from the installed library and its test
suite. It uses the actual TTS gRPC services with generated Python bindings. It does
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
uv run --project script/tts_spike pytest script/tts_spike/test_backend.py
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

## Work before a production backend

- Select Identity Server and Application Server endpoints independently. Hosted
  deployments can use different endpoints; this spike uses one local channel.
- Establish stream readiness and reauthentication behavior. Reconcile the initial
  device list with lifecycle events during connection setup.
- Map join, status, location and diagnostic events. The spike implements raw
  uplinks, device lifecycle, and positive/negative command acknowledgements.
- Decide how to support devices without a DevEUI. This adapter skips them because
  the current library identity requires one.
- Package or generate the official Python bindings reproducibly for distribution.
- Decide how to handle downlink expiry. The TTS API has no equivalent queue expiry
  field. The adapter rejects `expires_at` rather than silently discarding it.
  The current Dragino command methods specify expiry and therefore need a policy
  before they can work unchanged on TTS. A caller's timeout does not cancel an
  already queued command.

The generic connection protocol is unchanged apart from stack-aware brand filters.
`brands` contains `(stack, brand_id)` pairs. Descriptors include `stack`, `brand_id`
and `model_id`. A device class maps each supported stack to its native catalog
identity in `identifiers`; no cross-catalog translation table is needed.
