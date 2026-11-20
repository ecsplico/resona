// Who may read the calendar: a signed-in Directus user (verified against
// Directus /users/me with their own access token) whose email is on
// NUXT_VERLAUF_ALLOWED_EMAILS (empty list = every signed-in user).
import type { H3Event } from "h3";
import type { VerlaufConfig } from "./config";

/**
 * The caller's Directus access token: `Authorization: Bearer …`, or, for the
 * connect action (a browser navigation that cannot set headers), `?access_token=`.
 */
export function requestToken(event: H3Event, allowQuery: boolean): string {
	const header = getHeader(event, "authorization") ?? "";
	const match = /^Bearer\s+(\S+)$/i.exec(header);
	if (match?.[1]) return match[1];
	if (allowQuery) {
		const value = getQuery(event).access_token;
		if (typeof value === "string" && value) return value;
	}
	return "";
}

/** Throws 401 (no/invalid token) or 403 (not allow-listed); returns the user's id. */
export async function requireVerlaufUser(config: VerlaufConfig, token: string): Promise<string> {
	if (!token) {
		throw createError({ statusCode: 401, statusMessage: "Nicht angemeldet." });
	}
	let user: { id?: string; email?: string | null } | undefined;
	try {
		const me = await $fetch<{ data?: { id?: string; email?: string | null } }>(`${config.directusInternalUrl}/users/me`, {
			headers: { Authorization: `Bearer ${token}` },
			query: { fields: "id,email" },
		});
		user = me.data;
	}
	catch {
		throw createError({ statusCode: 401, statusMessage: "Anmeldung abgelaufen oder ungültig." });
	}
	if (!user?.id) {
		throw createError({ statusCode: 401, statusMessage: "Anmeldung abgelaufen oder ungültig." });
	}
	const email = (user.email ?? "").toLowerCase();
	if (config.allowedEmails.length > 0 && !config.allowedEmails.includes(email)) {
		throw createError({ statusCode: 403, statusMessage: "Dieses Konto ist für den Kalender nicht freigegeben." });
	}
	return user.id;
}
