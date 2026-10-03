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
      description: "Backend-neutral LoRaWAN events and device collections for Python.",
      plugins: [
        starlightLinksValidator(),
        starlightLlmsTxt({
          projectName: "lorawan-connection",
          description: "Read-only LoRaWAN event protocols, fixture dataclasses, and a device collection base for vendor libraries. No runtime dependencies.",
          details: [
            "- Requires Python 3.12 or later. Install with `pip install lorawan-connection`.",
            "- A caller owns the connection and forwards inventory and live events to a vendor DeviceCollection.",
            "- The collection creates supported models from catalog descriptors and notifies device-added listeners. Models interpret all events and expose typed state.",
            "- Payload Protocols accept matching generated messages by reference. Dispatch on EventType, not runtime Protocol checks.",
            "- ChirpStack transport helpers belong to the HA integration, not this package.",
            "- Building a library and Home Assistant integration are separate documentation sections. The HA provider API is currently a POC, not an upstream API.",
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
          { label: "Ownership and event delivery", slug: "connection/events" },
          { label: "Event reference", slug: "connection/reference" },
        ] },
        { label: "Device modelling", items: [
          { label: "Collections and models", slug: "modelling/overview" },
          { label: "Collection reference", slug: "modelling/reference" },
        ] },
        { label: "Building a library", items: [
          { label: "The device library", slug: "patterns/library" },
          { label: "Decoding and state", slug: "patterns/decoding" },
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
