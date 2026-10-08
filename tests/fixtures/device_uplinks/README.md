# Captured device uplinks

These raw application payloads came from physical S2101, S2102, and TS201 devices
on a ChirpStack deployment and were captured on 8 October 2026. They retain the
original payload bytes and FPorts. Device identifiers, server addresses, profile
UUIDs, and credentials are omitted.

Expected readings were checked against the vendor/TTN codec formats. Tests replay
these bytes through the Python model collections with synthetic descriptors and
official catalog identities. They do not contact the original server.

UC51x and LHT65 fixtures elsewhere in the tests come from published codecs. They
are not captured readings from this deployment.

Reference decoders independently reproduce these readings:

- [SenseCAP S210x TTN decoder](https://github.com/TheThingsNetwork/lorawan-devices/blob/7693223153369c9d6ded148c335b454cd318c756/vendor/sensecap/sensecap210x-common-decoder.js)
- [Milesight TS201 TTN decoder](https://github.com/TheThingsNetwork/lorawan-devices/blob/7693223153369c9d6ded148c335b454cd318c756/vendor/milesight-iot/ts201.js)
