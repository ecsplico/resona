// Google side of /api/termine: consent redirect, OAuth callback and the day's
// appointments. Token exchange, refresh and events.list are the layer's Google
// provider (resona-pwa server/utils/calendar/google.ts). Never logs event titles.
import type { H3Event } from "h3";
import { googleProvider } from "../../../../resona-pwa/server/utils/calendar/google";
import type { OAuthConfig } from "../../../../resona-pwa/server/utils/calendar/types";
import { dayBounds, filterTermine } from "~~/shared/termine";
import type { TermineView } from "~~/shared/termine";
import type { VerlaufConfig } from "./config";
import { clearConnection, readConnection, sealValue, unsealValue, writeConnection } from "./tokenStore";

const STATE_COOKIE = "verlauf_oauth";
const STATE_TTL_MS = 10 * 60 * 1000;

// The access token lives in memory only; one refresh at a time.
let accessCache: { token: string; expiresAt: number } | null = null;
let refreshing: Promise<string> | null = null;

interface OAuthState { nonce: string; user: string }

/** OAuth client config; 501 while NUXT_GOOGLE_CLIENT_ID/SECRET are missing. */
function oauthConfig(event: H3Event, config: VerlaufConfig): OAuthConfig {
	if (!config.google.clientId || !config.google.clientSecret) {
		throw createError({ statusCode: 501, statusMessage: "Google-Kalender ist nicht eingerichtet (NUXT_GOOGLE_CLIENT_ID/SECRET fehlen)." });
	}
	const origin = getRequestURL(event, { xForwardedHost: true, xForwardedProto: true }).origin;
	return {
		clientId: config.google.clientId,
		clientSecret: config.google.clientSecret,
		redirectUri: config.google.redirectUri || `${origin}/api/termine`,
	};
}

/**
 * Start the consent flow for the signed-in `user`. The state is sealed (10 min)
 * and its nonce also goes into an httpOnly cookie, so the callback only accepts
 * the browser that started it.
 */
export async function startConnect(event: H3Event, config: VerlaufConfig, user: string): Promise<void> {
	const oauth = oauthConfig(event, config);
	const nonce = globalThis.crypto.randomUUID();
	const state = await sealValue(config, { nonce, user } satisfies OAuthState, STATE_TTL_MS);
	setCookie(event, STATE_COOKIE, nonce, {
		httpOnly: true,
		sameSite: "lax",
		secure: getRequestURL(event, { xForwardedProto: true }).protocol === "https:",
		path: "/api/termine",
		maxAge: STATE_TTL_MS / 1000,
	});
	await sendRedirect(event, googleProvider.buildAuthUrl(oauth, state));
}

/** Google's redirect back: verify the state, store the refresh token, return to `/`. */
export async function handleOAuthCallback(event: H3Event, config: VerlaufConfig): Promise<void> {
	const query = getQuery(event);
	const cookieNonce = getCookie(event, STATE_COOKIE);
	deleteCookie(event, STATE_COOKIE, { path: "/api/termine" });
	if (query.error) {
		return sendRedirect(event, "/?kalender=abgebrochen");
	}
	const code = typeof query.code === "string" ? query.code : "";
	const sealedState = typeof query.state === "string" ? query.state : "";
	const state = sealedState ? await unsealValue<OAuthState>(config, sealedState) : null;
	if (!code || !state?.nonce || !state.user || state.nonce !== cookieNonce) {
		return sendRedirect(event, "/?kalender=fehler");
	}
	try {
		const tokens = await googleProvider.exchangeCode(oauthConfig(event, config), code);
		const refreshToken = tokens.refreshToken ?? (await readConnection(config))?.refreshToken;
		if (!refreshToken) {
			return sendRedirect(event, "/?kalender=fehler");
		}
		await writeConnection(config, { refreshToken, connectedBy: state.user, connectedAt: new Date().toISOString() });
		accessCache = { token: tokens.accessToken, expiresAt: tokens.expiresAt };
		return sendRedirect(event, "/?kalender=verbunden");
	}
	catch (err) {
		console.warn("[verlauf] Google code exchange failed", statusOf(err));
		return sendRedirect(event, "/?kalender=fehler");
	}
}

/** HTTP status of an ofetch/H3 error, if any. */
function statusOf(err: unknown): number | undefined {
	if (!err || typeof err !== "object") return undefined;
	const status = "status" in err ? err.status : "statusCode" in err ? err.statusCode : undefined;
	return typeof status === "number" ? status : undefined;
}

/** Google's token endpoint answered `{ error: "invalid_grant" }` (consent revoked/expired). */
function isInvalidGrant(err: unknown): boolean {
	if (!err || typeof err !== "object" || !("data" in err)) return false;
	const data = err.data;
	return !!data && typeof data === "object" && "error" in data && data.error === "invalid_grant";
}

/** A valid access token, refreshed (single-flight) shortly before expiry. */
async function accessToken(event: H3Event, config: VerlaufConfig, refreshToken: string): Promise<string> {
	if (accessCache && accessCache.expiresAt - 60_000 > Date.now()) {
		return accessCache.token;
	}
	refreshing ??= (async () => {
		try {
			const tokens = await googleProvider.refresh(oauthConfig(event, config), refreshToken);
			accessCache = { token: tokens.accessToken, expiresAt: tokens.expiresAt };
			return tokens.accessToken;
		}
		finally {
			refreshing = null;
		}
	})();
	return refreshing;
}

/** The day's EG/FG appointments; `connected: false` without a usable connection. */
export async function listTermine(event: H3Event, config: VerlaufConfig, day: string): Promise<TermineView> {
	const connection = await readConnection(config);
	if (!connection) {
		accessCache = null;
		return { connected: false, events: [] };
	}
	let token: string;
	try {
		token = await accessToken(event, config, connection.refreshToken);
	}
	catch (err) {
		if (isInvalidGrant(err)) {
			// Revoked or expired consent: forget it so the app offers "verbinden" again.
			await clearConnection(config);
			accessCache = null;
			return { connected: false, events: [] };
		}
		console.warn("[verlauf] Google token refresh failed", statusOf(err));
		return { connected: true, events: [], error: "load_failed" };
	}
	try {
		const events = await googleProvider.listEvents(token, { ...dayBounds(day), calendarId: config.calendarId });
		return { connected: true, events: filterTermine(events, config.titlePrefixes) };
	}
	catch (err) {
		if (statusOf(err) === 401) accessCache = null;
		console.warn("[verlauf] Google events.list failed", statusOf(err));
		return { connected: true, events: [], error: "load_failed" };
	}
}
