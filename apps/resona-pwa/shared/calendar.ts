// Calendar wire types + pure helpers, shared by the Vue app and the Nitro
// server routes (Nuxt 4 `shared/` dir, alias `#shared`). These describe the
// JSON exchanged over `/api/calendar/**`; both sides agree on this single
// definition. Provider-specific code (e.g. Google) normalizes INTO these.

/** A calendar event, normalized across providers. Times are ISO 8601 strings. */
export interface CalendarEvent {
	id: string;
	title: string;
	description?: string;
	/** ISO start — date-only (`2026-06-27`) when `allDay`, else a datetime. */
	start: string;
	/** ISO end — date-only when `allDay`, else a datetime. */
	end: string;
	allDay: boolean;
	location?: string;
	/** Provider deep-link to the event, when available. */
	htmlLink?: string;
}

/** Event-list filter understood by every provider's `listEvents`. */
export interface CalendarFilter {
	/** ISO lower bound (inclusive). */
	timeMin?: string;
	/** ISO upper bound (exclusive). */
	timeMax?: string;
	/** Free-text search query. */
	query?: string;
	/** Provider calendar id to read from; empty/undefined → the primary calendar. */
	calendarId?: string;
}

/** One selectable calendar in the connected account. */
export interface CalendarOption {
	id: string;
	summary: string;
	primary: boolean;
}

/** Response of `?action=calendars` — the account's calendars + connection state. */
export interface CalendarListView {
	configured: boolean;
	connected: boolean;
	calendars: CalendarOption[];
}

/** OAuth tokens persisted server-side (in the sealed session cookie). */
export interface TokenSet {
	accessToken: string;
	refreshToken?: string;
	/** Epoch milliseconds at which `accessToken` expires. */
	expiresAt: number;
	scope?: string;
	/** Connected account email, when the userinfo scope was granted. */
	email?: string;
}

/** Connection state for a provider. */
export interface CalendarStatus {
	provider: string;
	/** Whether the provider's OAuth client is configured on the server. */
	configured: boolean;
	/** Whether this browser has a stored, refreshable connection. */
	connected: boolean;
	email?: string;
}

/**
 * Response of `GET /api/calendar/:provider/events` — connection status plus the
 * matching events. Status and listing are merged into one call to keep the
 * server's typed route count small (Nitro's typed `$fetch` recurses over the
 * route union). `events` is empty when not connected; `error` flags a
 * post-connection load failure.
 */
export interface CalendarView extends CalendarStatus {
	events: CalendarEvent[];
	error?: string;
}

/**
 * Recording title for a calendar event: `"<event name> – <date>"`.
 * This is the name pre-filled when transcription starts from an event.
 */
export function eventDisplayTitle(event: CalendarEvent, locale = "de-DE"): string {
	const name = event.title?.trim() || "Termin";
	const date = formatEventDate(event.start, locale);
	return date ? `${name} – ${date}` : name;
}

/** ISO date (`YYYY-MM-DD`) → German display (`DD.MM.YYYY`); "" if blank/invalid. */
export function isoDateToGerman(iso: string): string {
	const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso.trim());
	return m ? `${m[3]}.${m[2]}.${m[1]}` : "";
}

/** German date (`TT.MM.JJJJ`, also `T.M.JJJJ`) → ISO (`YYYY-MM-DD`); "" if invalid. */
export function germanDateToIso(value: string): string {
	const m = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/.exec(value.trim());
	if (!m) {
		return "";
	}
	const day = Number(m[1]);
	const month = Number(m[2]);
	const iso = `${m[3]}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
	// Round-trip to reject impossible dates (e.g. 31.02.2026).
	const d = new Date(`${iso}T00:00:00`);
	return Number.isNaN(d.getTime()) || d.getDate() !== day || d.getMonth() + 1 !== month
		? ""
		: iso;
}

/** Format an ISO start as a short local date (e.g. `27.06.2026`); "" if unparseable. */
export function formatEventDate(iso: string, locale = "de-DE"): string {
	const d = new Date(iso);
	if (Number.isNaN(d.getTime())) {
		return "";
	}
	return new Intl.DateTimeFormat(locale, {
		day: "2-digit",
		month: "2-digit",
		year: "numeric",
	}).format(d);
}

/** Format an ISO start as a date + time label for display; falls back to the date. */
export function formatEventWhen(event: CalendarEvent, locale = "de-DE"): string {
	const date = formatEventDate(event.start, locale);
	if (event.allDay) {
		return date ? `${date} · ganztägig` : "ganztägig";
	}
	const d = new Date(event.start);
	if (Number.isNaN(d.getTime())) {
		return date;
	}
	const time = new Intl.DateTimeFormat(locale, {
		hour: "2-digit",
		minute: "2-digit",
	}).format(d);
	return date ? `${date} · ${time}` : time;
}
