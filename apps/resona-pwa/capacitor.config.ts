import type { CapacitorConfig } from "@capacitor/cli";

// Capacitor app shell around the static SPA build (`bun run cap:build`).
// Docs: docs/guide/pwa-app.md in the monorepo.
const config: CapacitorConfig = {
	appId: "de.ecsplico.resona",
	appName: "Resona",
	webDir: ".output/public",
	plugins: {
		// Android 15+ draws edge to edge. nuxt.config sets `viewport-fit=cover`; telling
		// SystemBars up front makes it pass the insets to `env(safe-area-inset-*)` from
		// the first frame instead of padding the WebView until it has read the meta tag.
		SystemBars: { initialViewportFitValueHint: "cover" },
	},
};

export default config;
