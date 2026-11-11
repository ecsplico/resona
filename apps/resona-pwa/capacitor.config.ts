import type { CapacitorConfig } from "@capacitor/cli";

// Capacitor app shell around the static SPA build (`bun run cap:build`).
// Docs: docs/guide/pwa-app.md in the monorepo.
const config: CapacitorConfig = {
	appId: "de.ecsplico.resona",
	appName: "Resona",
	webDir: ".output/public",
};

export default config;
