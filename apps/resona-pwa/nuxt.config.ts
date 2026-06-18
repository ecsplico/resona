import tailwindcss from "@tailwindcss/vite";

export default defineNuxtConfig({
	modules: ["@nuxt/eslint", "@nuxt/fonts", "@vueuse/nuxt", "@vite-pwa/nuxt"],
	devtools: { enabled: true },
	css: ["~/assets/css/main.css"],
	vite: { plugins: [tailwindcss()] },
	experimental: { typedPages: true },
	compatibilityDate: "2026-06-18",
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
	app: {
		head: {
			charset: "utf-8",
			viewport: "width=device-width, initial-scale=1, viewport-fit=cover",
			title: "Resona",
		},
	},
	fonts: {
		families: [
			{ name: "Fraunces", provider: "google" },
			{ name: "Instrument Sans", provider: "google" },
			{ name: "JetBrains Mono", provider: "google" },
		],
	},
	eslint: {
		config: {
			stylistic: { semi: true, quotes: "double", indent: "tab", commaDangle: "always-multiline" },
		},
	},
	pwa: { registerType: "autoUpdate", manifest: false, devOptions: { enabled: false } },
});
