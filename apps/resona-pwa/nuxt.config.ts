import tailwindcss from "@tailwindcss/vite";

export default defineNuxtConfig({
	modules: ["@nuxt/eslint", "@nuxt/fonts", "@vueuse/nuxt", "@vite-pwa/nuxt"],
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
		public: {
			directusUrl: "http://localhost:7700",
			resonaApiUrl: "http://localhost:7710",
			resonaApiKey: "",
			defaultLanguage: "de",
			defaultProfile: "default",
			defaultEngine: "faster-whisper",
		},
	},
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
	pwa: { registerType: "autoUpdate", manifest: false, devOptions: { enabled: false } },
});
