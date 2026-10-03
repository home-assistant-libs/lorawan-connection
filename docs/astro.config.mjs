// @ts-check
import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";
import starlightLinksValidator from "starlight-links-validator";
import starlightLlmsTxt from "starlight-llms-txt";

export default defineConfig({
  site: "https://home-assistant-libs.github.io",
  base: "/lorawan-connection",
  integrations: [
    starlight({
      title: "lorawan-connection",
      description: "A Python library for modelling LoRaWAN devices.",
      plugins: [
        starlightLinksValidator(),
        starlightLlmsTxt({
          projectName: "lorawan-connection",
          description: "A Python library for modelling LoRaWAN devices. Build device libraries with readings, commands, and update subscriptions. An optional ChirpStack connection provides inventory, live events, and queued downlinks.",
          details: [
            "- Requires Python 3.12 or later. Install with `pip install lorawan-connection`.",
            "- A caller owns the connection and forwards inventory and live events to a vendor DeviceCollection.",
            "- The collection creates supported models from catalog descriptors and notifies device-added listeners. Models interpret all events and expose typed state.",
            "- Payload Protocols accept matching generated messages by reference. Dispatch on EventType, not runtime Protocol checks.",
          ].join("\n"),
        }),
      ],
      customCss: ["@fontsource-variable/inter", "./src/styles/custom.css"],
      editLink: { baseUrl: "https://github.com/home-assistant-libs/lorawan-connection/edit/main/docs/" },
      social: [{ icon: "github", label: "GitHub", href: "https://github.com/home-assistant-libs/lorawan-connection" }],
      sidebar: [
        { label: "Getting started", items: [
          { label: "Introduction", slug: "index" },
          { label: "Installation", slug: "getting-started/installation" },
          { label: "Quickstart", slug: "getting-started/quickstart" },
        ] },
        { label: "Events and connections", items: [
          { label: "Understanding events", slug: "connection/events" },
          { label: "Connecting to ChirpStack", slug: "connection/chirpstack" },
          { label: "Event reference", slug: "connection/reference" },
        ] },
        { label: "Device modelling", items: [
          { label: "Devices and collections", slug: "modelling/overview" },
          { label: "Collection reference", slug: "modelling/reference" },
        ] },
        { label: "Building a library", items: [
          { label: "Build a device library", slug: "patterns/library" },
          { label: "Decoding and state", slug: "patterns/decoding" },
          { label: "Sending commands", slug: "patterns/commands" },
          { label: "Command-line helper", slug: "patterns/cli" },
          { label: "Testing", slug: "patterns/testing" },
        ] },
        { label: "Home Assistant", items: [
          { label: "Integration structure", slug: "home-assistant/integration" },
          { label: "Provider and discovery", slug: "home-assistant/provider" },
        ] },
      ],
    }),
  ],
});
