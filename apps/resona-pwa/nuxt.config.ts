import tailwindcss from "@tailwindcss/vite";

export default defineNuxtConfig({
	modules: ["@nuxt/eslint", "@nuxt/fonts", "@vueuse/nuxt", "@vite-pwa/nuxt"],
	ssr: false,
	components: [{ path: "~/components", pathPrefix: false }],
	devtools: { enabled: true },
	app: {
		head: {
			charset: "utf-8",
			viewport: "width=device-width, initial-scale=1, viewport-fit=cover",
			title: "Resona",
		},
	},
	css: ["~/assets/css/main.css"],
	runtimeConfig: {
		// Server-only secrets for the Google Calendar OAuth flow. These never reach
		// the browser — the Nitro `server/api/calendar/**` routes are the only
		// sanctioned server layer (see CLAUDE.md). Set via NUXT_GOOGLE_* env vars.
		google: {
			clientId: "",
			clientSecret: "",
			// Optional explicit override; when empty the callback URI is derived
			// from the incoming request origin.
			redirectUri: "",
		},
		// Password (>= 32 chars) sealing the per-browser session cookie that holds
		// the calendar refresh/access tokens. Set via NUXT_SESSION_PASSWORD.
		sessionPassword: "",
		public: {
			directusUrl: "http://localhost:7700",
			resonaApiUrl: "http://localhost:7710",
			resonaApiKey: "",
			defaultLanguage: "de",
			defaultProfile: "default",
			defaultEngine: "faster-whisper",
			calendarEnabled: true,
		},
	},
	devServer: { port: 7701 },
	experimental: { typedPages: true },
	compatibilityDate: "2026-06-18",
	vite: { plugins: [tailwindcss()] },
	eslint: {
		config: {
			stylistic: { semi: true, quotes: "double", indent: "tab", commaDangle: "always-multiline" },
		},
	},
	fonts: {
		families: [
			{ name: "Fraunces", provider: "google" },
			{ name: "Instrument Sans", provider: "google" },
			{ name: "JetBrains Mono", provider: "google" },
		],
	},
	pwa: {
		registerType: "autoUpdate",
		manifest: {
			name: "Resona",
			short_name: "Resona",
			description: "Resona — medizinisches Diktat: aufnehmen, transkribieren, exportieren.",
			theme_color: "#D96846",
			background_color: "#FBF5EC",
			display: "standalone",
			start_url: "/",
			icons: [
				{ src: "/pwa-192.png", sizes: "192x192", type: "image/png" },
				{ src: "/pwa-512.png", sizes: "512x512", type: "image/png" },
				{ src: "/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
			],
		},
		workbox: {
			runtimeCaching: [
				{ urlPattern: /\/assets\//, handler: "CacheFirst" },
			],
		},
		devOptions: { enabled: false },
	},
});
