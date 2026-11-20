import { describe, expect, it } from "vitest";
import type { CalendarEvent } from "#shared/calendar";
import {
	dayBounds,
	dayOf,
	filterTermine,
	formatDayLabel,
	formatTerminWhen,
	formatTime,
	groupByEvent,
	isDay,
	isEventId,
	isOffenWindow,
	isSamePatient,
	liveQuery,
	matchesPrefix,
	MAX_RANGE_DAYS,
	offenRange,
	openTermine,
	parseList,
	patientKey,
	rangeBounds,
	seriesId,
	shiftDay,
	shortText,
	splitContext,
	stripPrefix,
} from "../shared/termine";
import type { TerminEvent } from "../shared/termine";

function event(partial: Partial<CalendarEvent>): CalendarEvent {
	return { id: "e1", title: "EG Termin", start: "2026-11-19T09:30:00+01:00", end: "2026-11-19T10:20:00+01:00", allDay: false, ...partial };
}

describe("prefix filter", () => {
	it("parses the comma list", () => {
		expect(parseList(" EG, FG ,,")).toEqual(["EG", "FG"]);
		expect(parseList(undefined)).toEqual([]);
	});

	it("matches case-sensitive prefixes, ignoring leading blanks", () => {
		expect(matchesPrefix("EG Müller", ["EG", "FG"])).toBe(true);
		expect(matchesPrefix("  FG-Schmidt", ["EG", "FG"])).toBe(true);
		expect(matchesPrefix("eg Müller", ["EG", "FG"])).toBe(false);
		expect(matchesPrefix("Team EG", ["EG", "FG"])).toBe(false);
		expect(matchesPrefix("EG", [])).toBe(false);
	});

	it("keeps matching events in the wire shape only", () => {
		const result = filterTermine([
			event({ id: "a", title: "EG A", description: "Notiz", location: "Raum 1", htmlLink: "https://cal/a" }),
			event({ id: "b", title: "Mittag" }),
			event({ id: "c", title: "FG C" }),
		], ["EG", "FG"]);
		expect(result.map(e => e.id)).toEqual(["a", "c"]);
		expect(result[0]).toEqual({ id: "a", title: "EG A", start: "2026-11-19T09:30:00+01:00", end: "2026-11-19T10:20:00+01:00", allDay: false, htmlLink: "https://cal/a" });
		expect(result[1]).not.toHaveProperty("htmlLink");
	});
});

describe("days in Europe/Berlin", () => {
	it("validates days", () => {
		expect(isDay("2026-11-19")).toBe(true);
		expect(isDay("2026-02-30")).toBe(false);
		expect(isDay("19.11.2026")).toBe(false);
		expect(isDay(undefined)).toBe(false);
	});

	it("shifts across month and year ends", () => {
		expect(shiftDay("2026-11-30", 1)).toBe("2026-12-01");
		expect(shiftDay("2027-01-01", -1)).toBe("2026-12-31");
	});

	it("gives winter and summer day bounds", () => {
		expect(dayBounds("2026-11-19")).toEqual({ timeMin: "2026-11-18T23:00:00.000Z", timeMax: "2026-11-19T23:00:00.000Z" });
		expect(dayBounds("2026-07-01")).toEqual({ timeMin: "2026-06-30T22:00:00.000Z", timeMax: "2026-07-01T22:00:00.000Z" });
	});

	it("handles the DST switch days (23 h and 25 h)", () => {
		expect(dayBounds("2026-03-29")).toEqual({ timeMin: "2026-03-28T23:00:00.000Z", timeMax: "2026-03-29T22:00:00.000Z" });
		expect(dayBounds("2026-10-25")).toEqual({ timeMin: "2026-10-24T22:00:00.000Z", timeMax: "2026-10-25T23:00:00.000Z" });
	});

	it("finds the Berlin day of an instant", () => {
		expect(dayOf(new Date("2026-11-18T23:30:00Z"))).toBe("2026-11-19");
		expect(dayOf(new Date("2026-11-18T22:30:00Z"))).toBe("2026-11-18");
	});

	it("formats German labels and times", () => {
		expect(formatDayLabel("2026-11-19")).toBe("Donnerstag, 19. November 2026");
		expect(formatTime({ start: "2026-11-19T08:30:00Z", allDay: false })).toBe("09:30");
		expect(formatTime({ start: "2026-11-19", allDay: true })).toBe("ganztägig");
	});
});

describe("grouping and live link", () => {
	it("groups recordings by event id and skips unlinked ones", () => {
		const groups = groupByEvent([
			{ id: "r1", calendar_event_id: "a" },
			{ id: "r2", calendar_event_id: null },
			{ id: "r3", calendar_event_id: "a" },
			{ id: "r4", calendar_event_id: "b_20260603T133000Z" },
			{ id: "r5" },
		]);
		expect([...groups.keys()]).toEqual(["a", "b_20260603T133000Z"]);
		expect(groups.get("a")?.map(r => r.id)).toEqual(["r1", "r3"]);
	});

	it("builds the /live query with the Berlin date", () => {
		expect(liveQuery({ id: "a_20261119T083000Z", title: "EG Müller", start: "2026-11-19T08:30:00Z", end: "2026-11-19T09:20:00Z", allDay: false })).toEqual({
			title: "EG Müller – 19.11.2026",
			event: "a_20261119T083000Z",
			eventStart: "2026-11-19T08:30:00Z",
			eventTitle: "EG Müller",
		});
	});
});

function termin(partial: Partial<TerminEvent & { recurringEventId?: string }>): TerminEvent & { recurringEventId?: string } {
	return { id: "x", title: "EG Müller", start: "2026-11-19T08:30:00Z", end: "2026-11-19T09:20:00Z", allDay: false, ...partial };
}

describe("ranges", () => {
	it("turns inclusive Berlin days into one interval", () => {
		expect(rangeBounds("2026-10-01", "2026-10-31")).toEqual({ timeMin: "2026-09-30T22:00:00.000Z", timeMax: "2026-10-31T23:00:00.000Z" });
		expect(rangeBounds("2026-11-19", "2026-11-19")).toEqual(dayBounds("2026-11-19"));
	});

	it("refuses invalid, reversed and too long ranges", () => {
		expect(rangeBounds("2026-11-19", "2026-11-18")).toBeNull();
		expect(rangeBounds("2026-11-19", undefined)).toBeNull();
		expect(rangeBounds("2026-02-30", "2026-03-01")).toBeNull();
		expect(rangeBounds("2026-01-01", shiftDay("2026-01-01", MAX_RANGE_DAYS - 1))).not.toBeNull();
		expect(rangeBounds("2026-01-01", shiftDay("2026-01-01", MAX_RANGE_DAYS))).toBeNull();
	});

	it("knows the Offen windows", () => {
		expect(isOffenWindow("3m")).toBe(true);
		expect(isOffenWindow("1y")).toBe(false);
		expect(offenRange("4w", "2026-11-20")).toEqual({ from: "2026-10-23", to: "2026-11-20" });
		expect(offenRange("6m", "2026-11-20")).toEqual({ from: "2026-05-22", to: "2026-11-20" });
		const { from, to } = offenRange("6m", "2026-11-20");
		expect(rangeBounds(from, to)).not.toBeNull();
	});

	it("accepts Google event ids only", () => {
		expect(isEventId("abc123")).toBe(true);
		expect(isEventId("a1b2_20261119T083000Z")).toBe(true);
		expect(isEventId("EG Müller")).toBe(false);
		expect(isEventId("")).toBe(false);
		expect(isEventId(42)).toBe(false);
	});
});

describe("open appointments", () => {
	it("keeps past, undeclined, undictated ones, newest first", () => {
		const events = [
			termin({ id: "old", start: "2026-11-02T08:00:00Z" }),
			termin({ id: "new", start: "2026-11-18T08:00:00Z" }),
			termin({ id: "done", start: "2026-11-10T08:00:00Z" }),
			termin({ id: "no", start: "2026-11-11T08:00:00Z", declined: true }),
			termin({ id: "later", start: "2026-11-20T15:00:00Z" }),
		];
		const open = openTermine(events, new Set(["done"]), new Date("2026-11-20T12:00:00Z"));
		expect(open.map(e => e.id)).toEqual(["new", "old"]);
	});

	it("formats when, all-day included", () => {
		expect(formatTerminWhen({ start: "2026-11-19T08:30:00Z", allDay: false })).toBe("19.11.2026, 09:30");
		expect(formatTerminWhen({ start: "2026-11-19", allDay: true })).toBe("19.11.2026 ganztägig");
	});

	it("shortens previews", () => {
		expect(shortText("  a\n\nb  ")).toBe("a b");
		expect(shortText("abcdef", 3)).toBe("abc…");
	});
});

describe("same patient", () => {
	const prefixes = ["EG", "FG"];

	it("strips the prefix and normalizes the title", () => {
		expect(stripPrefix("  EG -  Müller,   Anna ", prefixes)).toBe("Müller, Anna");
		expect(stripPrefix("FG: Müller", prefixes)).toBe("Müller");
		expect(stripPrefix("Team", prefixes)).toBe("Team");
		expect(patientKey("FG  MÜLLER anna", prefixes)).toBe(patientKey("EG Müller Anna", prefixes));
	});

	it("derives the series id", () => {
		expect(seriesId({ id: "abc_20261119T083000Z" })).toBe("abc");
		expect(seriesId({ id: "abc_20261119T083000Z", recurringEventId: "base" })).toBe("base");
		expect(seriesId({ id: "single" })).toBe("single");
	});

	it("matches by series or title", () => {
		expect(isSamePatient(termin({ id: "s_1" }), termin({ id: "s_2", title: "FG Anders" }), prefixes)).toBe(true);
		expect(isSamePatient(termin({ id: "a" }), termin({ id: "b", title: "FG  MÜLLER" }), prefixes)).toBe(true);
		expect(isSamePatient(termin({ id: "a" }), termin({ id: "b", title: "EG Meier" }), prefixes)).toBe(false);
		expect(isSamePatient(termin({ id: "a", title: "EG" }), termin({ id: "b", title: "FG" }), prefixes)).toBe(false);
	});

	it("splits the context around the anchor, oldest first", () => {
		const anchor = termin({ id: "now", start: "2026-11-19T08:30:00Z" });
		const { before, after } = splitContext(anchor, [
			termin({ id: "later", start: "2027-01-10T08:00:00Z" }),
			termin({ id: "first", start: "2026-06-01T08:00:00Z" }),
			termin({ id: "now", start: "2026-11-19T08:30:00Z" }),
			termin({ id: "other", title: "EG Meier", start: "2026-10-01T08:00:00Z" }),
			termin({ id: "second", title: "FG Müller", start: "2026-09-01T08:00:00Z" }),
		], prefixes);
		expect(before.map(e => e.id)).toEqual(["first", "second"]);
		expect(after.map(e => e.id)).toEqual(["later"]);
	});
});
