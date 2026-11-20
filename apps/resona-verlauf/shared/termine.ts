// Pure helpers for Resona Verlauf, shared by the app and the /api/termine route.
// Imported as `~~/shared/termine` (this app's `#shared` alias points at the
// resona-pwa layer's shared dir, see nuxt.config.ts).
import type { CalendarEvent } from "#shared/calendar";

/** One appointment as `/api/termine` returns it. Times are ISO strings. */
export type TerminEvent = Pick<CalendarEvent, "id" | "title" | "start" | "end" | "allDay" | "htmlLink"> & {
	/** Marked "Kein Diktat" (no dictation expected anymore) via `POST /api/termine`. */
	declined?: true;
};

/** Response of `GET /api/termine?date=YYYY-MM-DD` and `?from=…&to=…`. */
export interface TermineView {
	/** A Google refresh token is stored on the server. */
	connected: boolean;
	events: TerminEvent[];
	/** Set when the connection exists but Google could not be read. */
	error?: "load_failed";
}

/** Response of `GET /api/termine?context=<event id>`: the same patient's other appointments. */
export interface TermineContextView {
	connected: boolean;
	/** Earlier appointments, oldest first (the newest sits closest to the recorder). */
	before: TerminEvent[];
	/** Later appointments, oldest first. */
	after: TerminEvent[];
	error?: "load_failed";
}

/** Body of `POST /api/termine`. */
export interface TerminAction {
	action: "decline" | "undecline";
	event: string;
}

/** Longest `from`…`to` span (inclusive days) `GET /api/termine` accepts. */
export const MAX_RANGE_DAYS = 200;

export const TIME_ZONE = "Europe/Berlin";

const DAY_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Comma list (`"EG,FG"`) → trimmed, non-empty entries. */
export function parseList(value: unknown): string[] {
	if (typeof value !== "string") return [];
	return value.split(",").map(part => part.trim()).filter(part => part.length > 0);
}

/** Title starts (after leading blanks) with one of the prefixes; case-sensitive. */
export function matchesPrefix(title: string, prefixes: readonly string[]): boolean {
	const trimmed = title.trimStart();
	return prefixes.some(prefix => trimmed.startsWith(prefix));
}

/** Keep only appointments whose title carries a prefix, reduced to the wire shape. */
export function filterTermine(events: readonly CalendarEvent[], prefixes: readonly string[]): TerminEvent[] {
	return events
		.filter(event => matchesPrefix(event.title, prefixes))
		.map(event => ({
			id: event.id,
			title: event.title,
			start: event.start,
			end: event.end,
			allDay: event.allDay,
			...(event.htmlLink ? { htmlLink: event.htmlLink } : {}),
		}));
}

/** `YYYY-MM-DD` naming a real calendar day. */
export function isDay(value: unknown): value is string {
	if (typeof value !== "string") return false;
	const m = DAY_RE.exec(value);
	if (!m) return false;
	const date = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
	return date.getUTCFullYear() === Number(m[1]) && date.getUTCMonth() === Number(m[2]) - 1 && date.getUTCDate() === Number(m[3]);
}

interface ZonedParts { year: number; month: number; day: number; hour: number; minute: number; second: number }

function zonedParts(instant: Date, timeZone: string): ZonedParts {
	const parts = new Intl.DateTimeFormat("en-US", {
		timeZone,
		hourCycle: "h23",
		year: "numeric",
		month: "2-digit",
		day: "2-digit",
		hour: "2-digit",
		minute: "2-digit",
		second: "2-digit",
	}).formatToParts(instant);
	const get = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find(p => p.type === type)?.value ?? 0);
	return { year: get("year"), month: get("month"), day: get("day"), hour: get("hour"), minute: get("minute"), second: get("second") };
}

/** Offset of `timeZone` from UTC at `instant`, in milliseconds (Berlin: +1 h / +2 h). */
function zoneOffsetMs(instant: Date, timeZone: string): number {
	const p = zonedParts(instant, timeZone);
	const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
	return asUtc - Math.floor(instant.getTime() / 1000) * 1000;
}

/** UTC instant of local midnight starting `day` in `timeZone`. */
function zonedMidnight(day: string, timeZone: string): Date {
	const m = DAY_RE.exec(day)!;
	const wallClock = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
	// First guess with the offset at UTC midnight, then once more with the offset
	// at that guess (the two differ only on a DST switch day).
	let guess = wallClock - zoneOffsetMs(new Date(wallClock), timeZone);
	guess = wallClock - zoneOffsetMs(new Date(guess), timeZone);
	return new Date(guess);
}

/** `day` shifted by `delta` calendar days (`YYYY-MM-DD`). */
export function shiftDay(day: string, delta: number): string {
	const m = DAY_RE.exec(day);
	if (!m) return day;
	const date = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + delta));
	return date.toISOString().slice(0, 10);
}

/** Start (inclusive) and end (exclusive) of a Berlin calendar day as UTC ISO strings. */
export function dayBounds(day: string, timeZone = TIME_ZONE): { timeMin: string; timeMax: string } {
	return {
		timeMin: zonedMidnight(day, timeZone).toISOString(),
		timeMax: zonedMidnight(shiftDay(day, 1), timeZone).toISOString(),
	};
}

/** The Berlin calendar day (`YYYY-MM-DD`) that contains `instant`. */
export function dayOf(instant: Date, timeZone = TIME_ZONE): string {
	const p = zonedParts(instant, timeZone);
	return `${p.year}-${String(p.month).padStart(2, "0")}-${String(p.day).padStart(2, "0")}`;
}

/** German day label, e.g. `Donnerstag, 19. November 2026`. */
export function formatDayLabel(day: string): string {
	if (!isDay(day)) return "";
	const noon = new Date(`${day}T12:00:00Z`);
	return new Intl.DateTimeFormat("de-DE", { timeZone: "UTC", weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(noon);
}

/** `HH:MM` in Berlin for a datetime; `ganztägig` for an all-day event; "" if invalid. */
export function formatTime(event: Pick<TerminEvent, "start" | "allDay">): string {
	if (event.allDay) return "ganztägig";
	const date = new Date(event.start);
	if (Number.isNaN(date.getTime())) return "";
	return new Intl.DateTimeFormat("de-DE", { timeZone: TIME_ZONE, hour: "2-digit", minute: "2-digit" }).format(date);
}

/** `dd.mm.yyyy HH:MM` in Berlin; "" if invalid. */
export function formatDateTime(iso: string | null | undefined): string {
	if (!iso) return "";
	const date = new Date(iso);
	if (Number.isNaN(date.getTime())) return "";
	return new Intl.DateTimeFormat("de-DE", {
		timeZone: TIME_ZONE,
		day: "2-digit",
		month: "2-digit",
		year: "numeric",
		hour: "2-digit",
		minute: "2-digit",
	}).format(date);
}

/** Recordings grouped by the appointment they belong to; unlinked ones are left out. */
export function groupByEvent<T extends { calendar_event_id?: string | null }>(recordings: readonly T[]): Map<string, T[]> {
	const groups = new Map<string, T[]>();
	for (const recording of recordings) {
		const id = recording.calendar_event_id;
		if (!id) continue;
		const group = groups.get(id);
		if (group) group.push(recording);
		else groups.set(id, [recording]);
	}
	return groups;
}

/** Query of `/live` for a dictation on `event`: preset title plus the event link. */
export function liveQuery(event: TerminEvent): { title: string; event: string; eventStart: string; eventTitle: string } {
	const name = event.title.trim() || "Termin";
	const day = event.allDay ? event.start.slice(0, 10) : dayOf(new Date(event.start));
	const date = isDay(day) ? `${day.slice(8, 10)}.${day.slice(5, 7)}.${day.slice(0, 4)}` : "";
	return {
		title: date ? `${name} – ${date}` : name,
		event: event.id,
		eventStart: event.start,
		eventTitle: event.title,
	};
}

/** Inclusive Berlin days `from`…`to` as one UTC interval; null if invalid, reversed or longer than `MAX_RANGE_DAYS`. */
export function rangeBounds(from: unknown, to: unknown, timeZone = TIME_ZONE): { timeMin: string; timeMax: string } | null {
	if (!isDay(from) || !isDay(to) || to < from) return null;
	const days = (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000 + 1;
	if (days > MAX_RANGE_DAYS) return null;
	return { timeMin: dayBounds(from, timeZone).timeMin, timeMax: dayBounds(to, timeZone).timeMax };
}

/** A Google event id (also a recurring instance `<base>_<YYYYMMDDTHHMMSSZ>`); never a title. */
export function isEventId(value: unknown): value is string {
	return typeof value === "string" && /^[\w-]{1,1024}$/.test(value);
}

/** Windows of the "Offen" tab, back from today. */
export const OFFEN_WINDOWS = [
	{ value: "4w", label: "4 Wochen", days: 28 },
	{ value: "3m", label: "3 Monate", days: 91 },
	{ value: "6m", label: "6 Monate", days: 182 },
] as const;

export type OffenWindow = (typeof OFFEN_WINDOWS)[number]["value"];

export function isOffenWindow(value: unknown): value is OffenWindow {
	return OFFEN_WINDOWS.some(w => w.value === value);
}

/** `from`/`to` (inclusive Berlin days) of an "Offen" window ending `today`. */
export function offenRange(window: OffenWindow, today: string): { from: string; to: string } {
	const days = OFFEN_WINDOWS.find(w => w.value === window)?.days ?? 28;
	return { from: shiftDay(today, -days), to: today };
}

/**
 * Past appointments still waiting for a dictation: started before `now`, not declined,
 * no recording linked. Newest first.
 */
export function openTermine(events: readonly TerminEvent[], dictated: ReadonlySet<string>, now: Date): TerminEvent[] {
	const limit = now.getTime();
	return events
		.filter(event => !event.declined && !dictated.has(event.id) && Date.parse(event.start) < limit)
		.sort((a, b) => Date.parse(b.start) - Date.parse(a.start));
}

/** The title without its leading prefix (`EG`/`FG`) and separators: the patient part, original case. */
export function stripPrefix(title: string, prefixes: readonly string[]): string {
	const trimmed = title.trim();
	const prefix = [...prefixes].sort((a, b) => b.length - a.length).find(p => trimmed.startsWith(p));
	const rest = prefix ? trimmed.slice(prefix.length) : trimmed;
	return rest.replace(/^[\s\-–—:.,/]+/, "").replace(/\s+/g, " ").trim();
}

/** Comparable patient key: title minus prefix, whitespace collapsed, case-insensitive. */
export function patientKey(title: string, prefixes: readonly string[]): string {
	return stripPrefix(title, prefixes).toLocaleLowerCase("de-DE");
}

/** Recurring series of an event: Google's `recurringEventId`, else the instance id before `_`. */
export function seriesId(event: { id: string; recurringEventId?: string }): string {
	if (event.recurringEventId) return event.recurringEventId;
	const cut = event.id.indexOf("_");
	return cut > 0 ? event.id.slice(0, cut) : event.id;
}

type SeriesEvent = TerminEvent & { recurringEventId?: string };

/** Same patient: same recurring series, or the same title apart from its prefix. */
export function isSamePatient(a: SeriesEvent, b: SeriesEvent, prefixes: readonly string[]): boolean {
	if (seriesId(a) === seriesId(b)) return true;
	const key = patientKey(a.title, prefixes);
	return key.length > 0 && key === patientKey(b.title, prefixes);
}

/** The anchor's same-patient appointments before and after it, each oldest first. */
export function splitContext<T extends SeriesEvent>(anchor: SeriesEvent, events: readonly T[], prefixes: readonly string[]): { before: T[]; after: T[] } {
	const at = Date.parse(anchor.start);
	const same = events
		.filter(event => event.id !== anchor.id && isSamePatient(anchor, event, prefixes))
		.sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
	return {
		before: same.filter(event => Date.parse(event.start) < at),
		after: same.filter(event => Date.parse(event.start) >= at),
	};
}

/** `text` cut to `max` characters with an ellipsis. */
export function shortText(text: string, max = 200): string {
	const flat = text.replace(/\s+/g, " ").trim();
	return flat.length <= max ? flat : `${flat.slice(0, max).trimEnd()}…`;
}

/** `dd.mm.yyyy HH:MM` in Berlin, or `dd.mm.yyyy ganztägig` for an all-day event. */
export function formatTerminWhen(event: Pick<TerminEvent, "start" | "allDay">): string {
	if (!event.allDay) return formatDateTime(event.start);
	const day = event.start.slice(0, 10);
	return isDay(day) ? `${day.slice(8, 10)}.${day.slice(5, 7)}.${day.slice(0, 4)} ganztägig` : "ganztägig";
}
