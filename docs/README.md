# lorawan-connection documentation

Astro/Starlight documentation, following the modbus-connection site structure.

```sh
cd docs
npm ci
npm run dev
npm run build
```

The development site is at `http://localhost:4321/lorawan-connection/`.
The build checks internal links and heading anchors. It also produces `llms.txt`
and `llms-full.txt`. GitHub Actions builds every branch and deploys `main` to
<https://home-assistant-libs.github.io/lorawan-connection/>.

Guides explain design and usage. Reference pages define signatures and behavior.
**Building a library** contains no HA code. **Home Assistant** explains the
consumer integration separately and identifies the provider API as a POC.
The quickstart and device-library pages embed tested source from `examples/`.
