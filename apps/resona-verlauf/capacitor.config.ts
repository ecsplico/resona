import type { CapacitorConfig } from "@capacitor/cli";

// Capacitor app shell around the static SPA build (`bun run cap:build`), like
// resona-pwa's. The static build has no Nitro server: set
// NUXT_PUBLIC_VERLAUF_API_BASE to the deployed Verlauf origin for /api/termine.
const config: CapacitorConfig = {
	appId: "de.ecsplico.resona.verlauf",
	appName: "Resona Verlauf",
	webDir: ".output/public",
	plugins: {
		SystemBars: { initialViewportFitValueHint: "cover" },
	},
};

export default config;
