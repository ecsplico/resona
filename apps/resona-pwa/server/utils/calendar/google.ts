// Google Calendar provider — talks to Google's OAuth + Calendar v3 REST APIs
// over `$fetch` (ofetch, global in Nitro) and normalizes into `CalendarEvent`.
//
// To add another provider, copy this shape into a sibling module and register
// it in `registry.ts`. The routes need no changes.
import type { CalendarEvent, CalendarFilter, CalendarOption, TokenSet } from "#shared/calendar";
import type { CalendarProvider, OAuthConfig } from "./types";

const AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth";
const TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token";
const REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke";
const USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo";
const CALENDAR_BASE = "https://www.googleapis.com/calendar/v3";
const CALENDAR_LIST_ENDPOINT = `${CALENDAR_BASE}/users/me/calendarList`;

/** events.list URL for a calendar id (defaults to the primary calendar). */
function eventsEndpoint(calendarId?: string): string {
	const id = calendarId?.trim() || "primary";
	return `${CALENDAR_BASE}/calendars/${encodeURIComponent(id)}/events`;
}

const SCOPES = [
	"https://www.googleapis.com/auth/calendar.readonly",
	"openid",
	"email",
] as const;

interface GoogleTokenResponse {
	access_token: string;
	expires_in: number;
	refresh_token?: string;
	scope?: string;
	id_token?: string;
}

interface GoogleEventDate {
	date?: string;
	dateTime?: string;
}

interface GoogleEvent {
	id: string;
	summary?: string;
	description?: string;
	location?: string;
	htmlLink?: string;
	status?: string;
	start?: GoogleEventDate;
	end?: GoogleEventDate;
}

interface GoogleCalendarListEntry {
	id: string;
	summary?: string;
	summaryOverride?: string;
	primary?: boolean;
	deleted?: boolean;
}

/** Normalize a calendarList entry into the shared option shape (pure — for tests). */
export function normalizeGoogleCalendar(raw: GoogleCalendarListEntry): CalendarOption {
	return {
		id: raw.id,
		summary: raw.summaryOverride ?? raw.summary ?? raw.id,
		primary: Boolean(raw.primary),
	};
}

/** Build the Google OAuth consent URL (pure — exported for tests). */
export function googleAuthUrl(config: OAuthConfig, state: string): string {
	const params = new URLSearchParams({
		client_id: config.clientId,
		redirect_uri: config.redirectUri,
		response_type: "code",
		scope: SCOPES.join(" "),
		// `offline` + `consent` are what make Google return a refresh token.
		access_type: "offline",
		prompt: "consent",
		include_granted_scopes: "true",
		state,
	});
	return `${AUTH_ENDPOINT}?${params.toString()}`;
}

/** Map Google's events.list filter into query params (pure — exported for tests). */
export function googleEventParams(filter: CalendarFilter): Record<string, string> {
	const params: Record<string, string> = {
		singleEvents: "true",
		orderBy: "startTime",
		maxResults: "50",
		timeMin: filter.timeMin ?? new Date().toISOString(),
	};
	if (filter.timeMax) {
		params.timeMax = filter.timeMax;
	}
	if (filter.query) {
		params.q = filter.query;
	}
	return params;
}

/** Normalize a raw Google event into the shared shape (pure — exported for tests). */
export function normalizeGoogleEvent(raw: GoogleEvent): CalendarEvent {
	const allDay = !raw.start?.dateTime;
	const start = raw.start?.dateTime ?? raw.start?.date ?? "";
	const end = raw.end?.dateTime ?? raw.end?.date ?? start;
	return {
		id: raw.id,
		title: raw.summary ?? "(ohne Titel)",
		description: raw.description,
		start,
		end,
		allDay,
		location: raw.location,
		htmlLink: raw.htmlLink,
	};
}

function toTokenSet(res: GoogleTokenResponse, fallbackRefresh?: string): TokenSet {
	return {
		accessToken: res.access_token,
		refreshToken: res.refresh_token ?? fallbackRefresh,
		expiresAt: Date.now() + res.expires_in * 1000,
		scope: res.scope,
	};
}

async function fetchEmail(accessToken: string): Promise<string | undefined> {
	try {
		const info = await $fetch<{ email?: string }>(USERINFO_ENDPOINT, {
			headers: { Authorization: `Bearer ${accessToken}` },
		});
		return info.email;
	}
	catch {
		// userinfo is best-effort; a connection without an email label is fine.
		return undefined;
	}
}

export const googleProvider: CalendarProvider = {
	name: "google",
	scopes: SCOPES,

	buildAuthUrl(config, state) {
		return googleAuthUrl(config, state);
	},

	async exchangeCode(config, code) {
		const res = await $fetch<GoogleTokenResponse>(TOKEN_ENDPOINT, {
			method: "POST",
			body: new URLSearchParams({
				code,
				client_id: config.clientId,
				client_secret: config.clientSecret,
				redirect_uri: config.redirectUri,
				grant_type: "authorization_code",
			}),
		});
		const tokens = toTokenSet(res);
		tokens.email = await fetchEmail(tokens.accessToken);
		return tokens;
	},

	async refresh(config, refreshToken) {
		const res = await $fetch<GoogleTokenResponse>(TOKEN_ENDPOINT, {
			method: "POST",
			body: new URLSearchParams({
				refresh_token: refreshToken,
				client_id: config.clientId,
				client_secret: config.clientSecret,
				grant_type: "refresh_token",
			}),
		});
		// Refresh responses omit the refresh_token — carry the existing one forward.
		return toTokenSet(res, refreshToken);
	},

	async listEvents(accessToken, filter) {
		const res = await $fetch<{ items?: GoogleEvent[] }>(eventsEndpoint(filter.calendarId), {
			headers: { Authorization: `Bearer ${accessToken}` },
			query: googleEventParams(filter),
		});
		return (res.items ?? [])
			.filter(e => e.status !== "cancelled")
			.map(normalizeGoogleEvent);
	},

	async listCalendars(accessToken) {
		const res = await $fetch<{ items?: GoogleCalendarListEntry[] }>(CALENDAR_LIST_ENDPOINT, {
			headers: { Authorization: `Bearer ${accessToken}` },
			// showHidden=true so secondary/hidden calendars surface; reader role so
			// freeBusy-only entries are excluded. maxResults bumped past the default 100.
			query: { minAccessRole: "reader", showHidden: "true", maxResults: "250" },
		});
		return (res.items ?? [])
			.filter(c => !c.deleted)
			.map(normalizeGoogleCalendar)
			// Primary first, then alphabetically — stable, picker-friendly order.
			.sort((a, b) => Number(b.primary) - Number(a.primary) || a.summary.localeCompare(b.summary));
	},

	async revoke(token) {
		await $fetch(REVOKE_ENDPOINT, {
			method: "POST",
			body: new URLSearchParams({ token }),
		});
	},
};
