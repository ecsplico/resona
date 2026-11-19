// Pure helpers for Resona Verlauf, shared by the app and the /api/termine route.
// Imported as `~~/shared/termine` (this app's `#shared` alias points at the
// resona-pwa layer's shared dir, see nuxt.config.ts).
import type { CalendarEvent } from "#shared/calendar";

/** One appointment as `/api/termine` returns it. Times are ISO strings. */
export type TerminEvent = Pick<CalendarEvent, "id" | "title" | "start" | "end" | "allDay" | "htmlLink">;

/** Response of `GET /api/termine?date=YYYY-MM-DD`. */
export interface TermineView {
	/** A Google refresh token is stored on the server. */
	connected: boolean;
	events: TerminEvent[];
	/** Set when the connection exists but Google could not be read. */
	error?: "load_failed";
}

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
