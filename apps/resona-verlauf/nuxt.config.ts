import { fileURLToPath } from "node:url";

// Resona Verlauf is a Nuxt layer app on top of ../resona-pwa: pages, components,
// composables, plugins, middleware, server routes and public assets come from the
// layer; this app adds the appointment-centred home, /termin/:id, /desktop and
// the server-side calendar route /api/termine (see CLAUDE.md).
//
// Aliases (`~`, `~~`, `#shared`) inside the layer's nuxt.config resolve against
// THIS app, not the layer. Hence the explicit layer paths below: the layer's
// components dir, its `shared/` dir behind `#shared`, and app/assets/css/main.css
// here forwarding to the layer stylesheet.
const pwaDir = fileURLToPath(new URL("../resona-pwa/", import.meta.url));

export default defineNuxtConfig({
	extends: ["../resona-pwa"],
	ssr: false,
	components: [
		{ path: "~/components", pathPrefix: false },
		{ path: `${pwaDir}app/components`, pathPrefix: false },
	],
	app: {
		head: {
			title: "Resona Verlauf",
		},
	},
	runtimeConfig: {
		// Server-only (never reach the browser). The layer already declares
		// google.{clientId,clientSecret,redirectUri} (NUXT_GOOGLE_*), sessionPassword
		// (NUXT_SESSION_PASSWORD) and directusInternalUrl (NUXT_DIRECTUS_INTERNAL_URL);
		// here the redirect URI is `<origin>/api/termine` when empty.
		verlauf: {
			// NUXT_VERLAUF_CALENDAR_ID: Google calendar id to read ("" = primary).
			calendarId: "",
			// NUXT_VERLAUF_TITLE_PREFIXES: comma list; only events whose title starts with one count.
			titlePrefixes: "EG,FG",
			// NUXT_VERLAUF_ALLOWED_EMAILS: comma list of Directus user emails ("" = any signed-in user).
			allowedEmails: "",
			// NUXT_VERLAUF_DATA_DIR: directory holding the sealed Google refresh token.
			dataDir: ".data/verlauf",
		},
		public: {
			// The old /calendar page and /api/calendar stay off; /api/termine replaces them.
			calendarEnabled: false,
			// Base URL of /api/termine ("" = same origin). The Capacitor build has no
			// server of its own, so it points this at the deployed Verlauf origin.
			verlaufApiBase: "",
		},
	},
	alias: {
		// The layer's code (app and server) imports `#shared/calendar`, `#shared/errors`, …
		// This app's own shared helpers are imported as `~~/shared/termine`.
		"#shared": `${pwaDir}shared/`,
	},
	devServer: { port: 7731 },
	hooks: {
		// Typecheck only: the layer's own `~/…` imports resolve against this app's
		// tsconfig paths, so add the layer's app dir as a fallback (Vite already
		// resolves them per layer). Nuxt regenerates `paths` from aliases, so this
		// has to run on the generated configs rather than via `typescript.tsConfig`.
		"prepare:types"(ctx) {
			const configs = [ctx.tsConfig, ctx.sharedTsConfig, ctx.serverTsConfig] as Array<{ compilerOptions?: { paths?: Record<string, string[]> } } | undefined>;
			for (const config of configs) {
				const paths = config?.compilerOptions?.paths;
				if (!paths) continue;
				for (const key of ["~/*", "@/*"]) {
					const own = paths[key];
					if (!own) continue;
					const layer = `${pwaDir}app/*`;
					if (!own.includes(layer)) own.push(layer);
				}
			}
		},
	},
	pwa: {
		manifest: {
			name: "Resona Verlauf",
			short_name: "Verlauf",
			description: "Resona Verlauf — Diktate zu den Terminen des Tages.",
		},
	},
});
