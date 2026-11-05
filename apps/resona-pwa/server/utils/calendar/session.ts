// Session + OAuth-config helpers shared by the calendar routes.
//
// Tokens are stored per-browser in an h3 sealed (encrypted) session cookie —
// the PWA has no server database, and the refresh token must not reach client
// JS. Keyed by provider name so multiple providers can be connected at once.
import type { H3Event } from "h3";
import type { TokenSet } from "#shared/calendar";
import type { CalendarProvider, OAuthConfig } from "./types";

interface CalendarSessionData {
	/** OAuth CSRF state, set at connect, verified at callback. */
	oauthState?: string;
	/** Stored token set per provider name. */
	tokens?: Record<string, TokenSet>;
}

/** Resolve OAuth credentials for `provider` from runtimeConfig. */
export function oauthConfig(event: H3Event, provider: string): OAuthConfig {
	const rc = useRuntimeConfig(event) as Record<string, { clientId?: string; clientSecret?: string; redirectUri?: string }>;
	const creds = rc[provider];
	if (!creds?.clientId || !creds?.clientSecret) {
		throw createError({
			statusCode: 501,
			statusMessage: `Calendar provider '${provider}' is not configured (missing client id/secret).`,
		});
	}
	// All providers share the single `/api/calendar` callback path; the provider
	// is carried in the OAuth `state`, so the redirect URI is provider-less.
	const redirectUri
		= creds.redirectUri
			|| `${getRequestURL(event).origin}/api/calendar`;
	return {
		clientId: creds.clientId,
		clientSecret: creds.clientSecret,
		redirectUri,
	};
}

/** True if `provider` has usable OAuth credentials configured. */
export function isProviderConfigured(event: H3Event, provider: string): boolean {
	try {
		oauthConfig(event, provider);
		return true;
	}
	catch {
		return false;
	}
}

/** Open the sealed calendar session. Throws 501 if no session password is set. */
export async function calendarSession(event: H3Event) {
	const password = useRuntimeConfig(event).sessionPassword as string;
	if (!password || password.length < 32) {
		throw createError({
			statusCode: 501,
			statusMessage: "Calendar session is not configured (set NUXT_SESSION_PASSWORD to a 32+ char secret).",
		});
	}
	return useSession<CalendarSessionData>(event, { password, name: "resona-calendar" });
}

/** Read the stored token set for `provider`, if any. */
export async function readTokens(event: H3Event, provider: string): Promise<TokenSet | undefined> {
	const session = await calendarSession(event);
	return session.data.tokens?.[provider];
}

/** Persist (or clear, when `tokens` is undefined) the token set for `provider`. */
export async function writeTokens(event: H3Event, provider: string, tokens: TokenSet | undefined): Promise<void> {
	const session = await calendarSession(event);
	const current = session.data.tokens ?? {};
	const next = Object.fromEntries(
		Object.entries(current).filter(([key]) => key !== provider),
	);
	if (tokens) {
		next[provider] = tokens;
	}
	await session.update({ tokens: next });
}

/**
 * Return a valid access token for `provider`, refreshing transparently when the
 * stored one is expired. Throws 401 when the browser is not connected.
 */
export async function requireAccessToken(event: H3Event, provider: CalendarProvider): Promise<string> {
	const tokens = await readTokens(event, provider.name);
	if (!tokens?.refreshToken && !tokens?.accessToken) {
		throw createError({ statusCode: 401, statusMessage: "Calendar not connected." });
	}
	// 30s skew margin so we never hand out an about-to-expire token.
	if (tokens.accessToken && tokens.expiresAt - 30_000 > Date.now()) {
		return tokens.accessToken;
	}
	if (!tokens.refreshToken) {
		throw createError({ statusCode: 401, statusMessage: "Calendar session expired — reconnect." });
	}
	const config = oauthConfig(event, provider.name);
	const refreshed = await provider.refresh(config, tokens.refreshToken);
	// Carry the connected email label forward across refreshes.
	refreshed.email = refreshed.email ?? tokens.email;
	await writeTokens(event, provider.name, refreshed);
	return refreshed.accessToken;
}
