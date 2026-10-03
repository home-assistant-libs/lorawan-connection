---
title: Decoding and state
description: Decode complete frames before updating typed model state.
---

A decoder takes bytes and returns measurements. It does not publish entities,
call the network, or modify a model during parsing. Validate the whole frame before
merging any returned values.

The shared package supplies event contracts, collection lifecycle, and callback
helpers. Byte layouts, units, sentinels, checksums, and valid ports belong to the
vendor library. The library has no generic binary-parser or QR-parser API.
Use Python's `int.from_bytes` and `struct` when they fit the wire format.

## SenseCAP S2101 example

The example reads seven-byte measurement records followed by a two-byte trailer:

| Bytes within a record | Interpretation |
| --- | --- |
| 0 | Channel number; this example accepts channel 1. |
| 1–2 | Little-endian measurement identifier. |
| 3–6 | Signed little-endian value, divided by 1000. |

Measurement `4097` is temperature in degrees Celsius. Measurement `4098` is
relative humidity in percent. The example validates temperature from −40 to 85 °C
and humidity from 0 to 100%. Unknown channels and measurement identifiers are ignored.

A frame needs at least one record and the trailer. A malformed length or invalid
known measurement raises `ValueError`. The model catches it and preserves its
previous state. A valid partial frame updates only its included measurements.

The example follows the [Seeed SenseCAP reference decoders](https://github.com/Seeed-Solution/SenseCAP-Decoder).
The POC exercised captured sample bytes through a real ChirpStack server and a
simulated radio gateway. It did not test physical SenseCAP hardware.

The trailer is retained in the frame layout but is not validated: the reference
CRC routine used for the POC was a stub. Real captures and vendor confirmation of
trailer semantics and error sentinels are needed before treating this example as
a production SenseCAP library.

## Partial state and time

The example keeps a last-update timestamp for each measurement. An older event
cannot overwrite a newer measurement. Equal timestamps are accepted, because two
partial readings can share a receipt timestamp.

A newer temperature-only event leaves humidity unchanged. A zero is a measurement;
it is never treated as missing. The model notifies state listeners only when its
frozen state dataclass changes.

These rules belong to the model. The shared collection does not impose timestamp
ordering, deduplication, or device-specific stale-data rules.

## Adding another model

Collect its catalog identity, firmware-specific wire format, and captured frames.
Implement a decoder and typed state. Test malformed data, sentinel values, partial
updates, and every event type that model uses. Then add the model class
to `SUPPORTED_MODELS`; its class attributes supply the catalog identity.

Keep all FPort interpretation here. The provider forwards events for every port;
an entity should never need to know the device's FPort.
