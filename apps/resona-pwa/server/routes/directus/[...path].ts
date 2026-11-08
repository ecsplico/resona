// Same-origin proxy to Directus, so Directus needs no public host (and its admin
// UI stays unreachable). Only the API paths the PWA uses are forwarded; the
// SSO callback and the refresh-token cookie are first-party because of this.
const ALLOWED = [
	/^\/auth\/(login|refresh|logout)(\/|$)/,
	/^\/items\/(recordings|transcripts|documents)(\/|$)/,
	/^\/files(\/|$)/,
	/^\/assets\/[\w-]+$/,
	/^\/users\/me$/,
	/^\/server\/(health|ping)$/,
];

export default defineEventHandler((event) => {
	const base = useRuntimeConfig(event).directusInternalUrl;
	const path = `/${getRouterParam(event, "path") ?? ""}`;
	if (!base || !ALLOWED.some(re => re.test(path))) {
		throw createError({ statusCode: 404, statusMessage: "Not Found" });
	}
	const query = getRequestURL(event).search;
	// Directus answers the SSO start/callback with 302s meant for the browser.
	return proxyRequest(event, `${base.replace(/\/$/, "")}${path}${query}`, { fetchOptions: { redirect: "manual" } });
});
