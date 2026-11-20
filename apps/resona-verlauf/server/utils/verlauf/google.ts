// Google side of /api/termine: consent redirect, OAuth callback, appointments in a
// range and one appointment's same-patient context. Token exchange and refresh are
// the layer's Google provider (resona-pwa server/utils/calendar/google.ts); the
// event reads page through Google here (the layer's listEvents stops at 50).
// Never logs event titles.
import type { H3Event } from "h3";
import type { CalendarEvent } from "#shared/calendar";
import { eventsEndpoint, googleEventParams, googleProvider, normalizeGoogleEvent } from "../../../../resona-pwa/server/utils/calendar/google";
import type { OAuthConfig } from "../../../../resona-pwa/server/utils/calendar/types";
import { filterTermine, matchesPrefix, splitContext, stripPrefix } from "~~/shared/termine";
import type { TermineContextView, TerminEvent, TermineView } from "~~/shared/termine";
import type { VerlaufConfig } from "./config";
import { clearConnection, readConnection, readDeclines, sealValue, unsealValue, writeConnection } from "./tokenStore";
import type { Decline } from "./tokenStore";

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

/** Outcome of one Google read: no connection, a failed read, or the value. */
type CalendarRead<T> = { connected: false } | { connected: true; failed: true } | { connected: true; failed: false; value: T };

/**
 * Run `read` with a valid access token. A missing or revoked connection answers
 * `connected: false` (revoked consent is forgotten, so the app offers "verbinden"
 * again); token or Google failures answer `failed` and log only the HTTP status.
 */
async function withCalendar<T>(event: H3Event, config: VerlaufConfig, read: (token: string) => Promise<T>): Promise<CalendarRead<T>> {
	const connection = await readConnection(config);
	if (!connection) {
		accessCache = null;
		return { connected: false };
	}
	let token: string;
	try {
		token = await accessToken(event, config, connection.refreshToken);
	}
	catch (err) {
		if (isInvalidGrant(err)) {
			await clearConnection(config);
			accessCache = null;
			return { connected: false };
		}
		console.warn("[verlauf] Google token refresh failed", statusOf(err));
		return { connected: true, failed: true };
	}
	try {
		return { connected: true, failed: false, value: await read(token) };
	}
	catch (err) {
		if (statusOf(err) === 401) accessCache = null;
		console.warn("[verlauf] Google events read failed", statusOf(err));
		return { connected: true, failed: true };
	}
}

type RawGoogleEvent = Parameters<typeof normalizeGoogleEvent>[0] & { recurringEventId?: string };
/** A normalized event that still knows its recurring series (server-side only). */
type SeriesCalendarEvent = CalendarEvent & { recurringEventId?: string };

const PAGE_SIZE = "250";
// 20 × 250 events: far beyond a practice's 200 days; stops a runaway loop.
const MAX_PAGES = 20;
const DAY_MS = 86_400_000;
const CONTEXT_BACK_MS = 365 * DAY_MS;
const CONTEXT_FORWARD_MS = 183 * DAY_MS;

function toEvent(raw: RawGoogleEvent): SeriesCalendarEvent {
	const event = normalizeGoogleEvent(raw);
	return raw.recurringEventId ? { ...event, recurringEventId: raw.recurringEventId } : event;
}

/** Every non-cancelled event of a Google list endpoint, following `nextPageToken`. */
async function fetchPaged(token: string, url: string, query: Record<string, string>): Promise<SeriesCalendarEvent[]> {
	const events: SeriesCalendarEvent[] = [];
	let pageToken: string | undefined;
	for (let page = 0; page < MAX_PAGES; page++) {
		const res = await $fetch<{ items?: RawGoogleEvent[]; nextPageToken?: string }>(url, {
			headers: { Authorization: `Bearer ${token}` },
			query: { ...query, maxResults: PAGE_SIZE, ...(pageToken ? { pageToken } : {}) },
		});
		for (const raw of res.items ?? []) {
			if (raw.status !== "cancelled") events.push(toEvent(raw));
		}
		pageToken = res.nextPageToken;
		if (!pageToken) break;
	}
	return events;
}

/** One event by id; null when Google does not know it (404/410) or it was cancelled. */
async function getEvent(token: string, config: VerlaufConfig, id: string): Promise<SeriesCalendarEvent | null> {
	try {
		const raw = await $fetch<RawGoogleEvent>(`${eventsEndpoint(config.calendarId)}/${encodeURIComponent(id)}`, {
			headers: { Authorization: `Bearer ${token}` },
		});
		return raw.status === "cancelled" ? null : toEvent(raw);
	}
	catch (err) {
		const status = statusOf(err);
		if (status === 404 || status === 410) return null;
		throw err;
	}
}

/** Flag the events marked "Kein Diktat". */
function markDeclined(events: TerminEvent[], declines: Record<string, Decline>): TerminEvent[] {
	return events.map(event => (declines[event.id] ? { ...event, declined: true } : event));
}

/** EG/FG appointments between `timeMin` and `timeMax`; `connected: false` without a usable connection. */
export async function listTermine(event: H3Event, config: VerlaufConfig, bounds: { timeMin: string; timeMax: string }): Promise<TermineView> {
	const declines = await readDeclines(config);
	const read = await withCalendar(event, config, token => fetchPaged(token, eventsEndpoint(config.calendarId), googleEventParams(bounds)));
	if (!read.connected) return { connected: false, events: [] };
	if (read.failed) return { connected: true, events: [], error: "load_failed" };
	return { connected: true, events: markDeclined(filterTermine(read.value, config.titlePrefixes), declines) };
}

/**
 * The same patient's other EG/FG appointments around `eventId` (12 months back,
 * 6 forward): Google full-text search on the title without its prefix, plus the
 * instances of its recurring series. The search term never leaves the server.
 */
export async function termineContext(event: H3Event, config: VerlaufConfig, eventId: string): Promise<TermineContextView> {
	const declines = await readDeclines(config);
	const read = await withCalendar(event, config, async (token) => {
		const anchor = await getEvent(token, config, eventId);
		if (!anchor) return { before: [], after: [] };
		const at = Date.parse(anchor.start);
		const window = { timeMin: new Date(at - CONTEXT_BACK_MS).toISOString(), timeMax: new Date(at + CONTEXT_FORWARD_MS).toISOString() };
		const query = stripPrefix(anchor.title, config.titlePrefixes);
		const [found, series] = await Promise.all([
			query ? fetchPaged(token, eventsEndpoint(config.calendarId), googleEventParams({ ...window, query })) : Promise.resolve([]),
			anchor.recurringEventId
				? fetchPaged(token, `${eventsEndpoint(config.calendarId)}/${encodeURIComponent(anchor.recurringEventId)}/instances`, window)
				: Promise.resolve([]),
		]);
		const unique = new Map<string, SeriesCalendarEvent>();
		for (const candidate of [...found, ...series]) {
			if (matchesPrefix(candidate.title, config.titlePrefixes)) unique.set(candidate.id, candidate);
		}
		return splitContext(anchor, [...unique.values()], config.titlePrefixes);
	});
	if (!read.connected) return { connected: false, before: [], after: [] };
	if (read.failed) return { connected: true, before: [], after: [], error: "load_failed" };
	const wire = (events: SeriesCalendarEvent[]) => markDeclined(filterTermine(events, config.titlePrefixes), declines);
	return { connected: true, before: wire(read.value.before), after: wire(read.value.after) };
}
