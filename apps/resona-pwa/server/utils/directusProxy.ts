// Same-origin proxy to Directus, so Directus needs no public host (and its admin
// UI stays unreachable). Only the API paths the PWA uses are forwarded; the
// SSO callback and the refresh-token cookie are first-party because of this.
//
// Explicit h3 imports (instead of Nitro auto-imports) keep it runnable in the spec.
import type { H3Event } from "h3";
import { appendResponseHeader, createError, getRequestURL, proxyRequest } from "h3";

const ALLOWED = [
	/^\/auth\/(login|refresh|logout)(\/|$)/,
	/^\/items\/(recordings|transcripts|documents)(\/|$)/,
	/^\/files(\/|$)/,
	/^\/assets\/[\w-]+$/,
	/^\/users\/me$/,
	/^\/server\/(health|ping)$/,
];

/** Directus' default REFRESH_TOKEN_COOKIE_NAME. */
export const REFRESH_COOKIE = "directus_refresh_token";

/**
 * Deletes the host-only refresh cookie (no Domain attribute), leaving a cookie of
 * the same name on the parent domain alone. Directus set it host-only until
 * REFRESH_TOKEN_COOKIE_DOMAIN was configured; a browser keeping both sends the
 * older, host-only one first, Directus reads the first and every refresh is 401,
 * even right after a fresh SSO login.
 */
export const EXPIRE_HOST_ONLY_REFRESH_COOKIE
	= `${REFRESH_COOKIE}=; Path=/; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; Secure; SameSite=Lax`;

export interface DirectusProxyConfig {
	/** Server-side Directus URL; empty = 404. */
	base: string;
	/**
	 * Directus' REFRESH_TOKEN_COOKIE_DOMAIN. When set, a host-only refresh cookie is
	 * always a leftover, so every /auth/* response expires it. Empty = the host-only
	 * cookie is the live one and is left alone.
	 */
	cookieDomain: string;
}

export function proxyDirectus(event: H3Event, path: string, config: DirectusProxyConfig) {
	if (!config.base || !ALLOWED.some(re => re.test(path))) {
		throw createError({ statusCode: 404, statusMessage: "Not Found" });
	}
	const query = getRequestURL(event).search;
	const clearHostOnly = Boolean(config.cookieDomain) && path.startsWith("/auth/");
	// Directus answers the SSO start/callback with 302s meant for the browser.
	return proxyRequest(event, `${config.base.replace(/\/$/, "")}${path}${query}`, {
		fetchOptions: { redirect: "manual" },
		// After Directus' own Set-Cookie, which (with a domain) targets another cookie.
		onResponse: clearHostOnly
			? (e) => { appendResponseHeader(e, "set-cookie", EXPIRE_HOST_ONLY_REFRESH_COOKIE); }
			: undefined,
	});
}
