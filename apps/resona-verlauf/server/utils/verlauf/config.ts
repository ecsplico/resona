// Server-only configuration of /api/termine, read from runtimeConfig (env
// NUXT_GOOGLE_*, NUXT_SESSION_PASSWORD, NUXT_DIRECTUS_INTERNAL_URL, NUXT_VERLAUF_*).
import type { H3Event } from "h3";
import { parseList } from "~~/shared/termine";

export interface VerlaufConfig {
	google: { clientId: string; clientSecret: string; redirectUri: string };
	/** Seals the stored refresh token and the OAuth state; >= 32 chars. */
	sessionPassword: string;
	directusInternalUrl: string;
	calendarId: string;
	titlePrefixes: string[];
	/** Lower-cased; empty = any signed-in Directus user. */
	allowedEmails: string[];
	dataDir: string;
}

interface RawConfig {
	google?: { clientId?: string; clientSecret?: string; redirectUri?: string };
	sessionPassword?: string;
	directusInternalUrl?: string;
	verlauf?: { calendarId?: string; titlePrefixes?: string; allowedEmails?: string; dataDir?: string };
}

/**
 * The route's configuration. Throws 501 when the parts every request needs are
 * missing (session password, Directus URL); Google credentials are checked where
 * they are used, so a list request without them still answers `connected: false`.
 */
export function verlaufConfig(event: H3Event): VerlaufConfig {
	const rc = useRuntimeConfig(event) as unknown as RawConfig;
	const sessionPassword = rc.sessionPassword ?? "";
	if (sessionPassword.length < 32) {
		throw createError({ statusCode: 501, statusMessage: "Kalender ist nicht eingerichtet (NUXT_SESSION_PASSWORD fehlt oder ist kürzer als 32 Zeichen)." });
	}
	const directusInternalUrl = (rc.directusInternalUrl ?? "").replace(/\/$/, "");
	if (!directusInternalUrl) {
		throw createError({ statusCode: 501, statusMessage: "Kalender ist nicht eingerichtet (NUXT_DIRECTUS_INTERNAL_URL fehlt)." });
	}
	return {
		google: {
			clientId: rc.google?.clientId ?? "",
			clientSecret: rc.google?.clientSecret ?? "",
			redirectUri: rc.google?.redirectUri ?? "",
		},
		sessionPassword,
		directusInternalUrl,
		calendarId: rc.verlauf?.calendarId ?? "",
		titlePrefixes: parseList(rc.verlauf?.titlePrefixes ?? "EG,FG"),
		allowedEmails: parseList(rc.verlauf?.allowedEmails ?? "").map(email => email.toLowerCase()),
		dataDir: rc.verlauf?.dataDir || ".data/verlauf",
	};
}
