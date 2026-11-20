// CORS for /api/termine, only for the Capacitor app shell: its static build has no
// server, so it calls the deployed Verlauf origin (runtimeConfig.public.verlaufApiBase)
// from `https://localhost` (Android) or `capacitor://localhost` (iOS). The web app
// calls same-origin and needs none of this. Auth stays the Bearer Directus token.
const APP_ORIGINS = ["https://localhost", "capacitor://localhost"];

export default defineEventHandler((event) => {
	if (getRequestURL(event).pathname !== "/api/termine") return;
	const origin = getHeader(event, "origin");
	if (!origin || !APP_ORIGINS.includes(origin)) return;
	setResponseHeaders(event, {
		"Access-Control-Allow-Origin": origin,
		"Access-Control-Allow-Methods": "GET, POST, OPTIONS",
		"Access-Control-Allow-Headers": "Authorization, Content-Type",
		"Access-Control-Max-Age": "600",
		"Vary": "Origin",
	});
	if (event.method === "OPTIONS") {
		setResponseStatus(event, 204);
		return "";
	}
});
