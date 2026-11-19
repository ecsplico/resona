import { describe, expect, it } from "vitest";
import type { CalendarEvent } from "#shared/calendar";
import {
	dayBounds,
	dayOf,
	filterTermine,
	formatDayLabel,
	formatTime,
	groupByEvent,
	isDay,
	liveQuery,
	matchesPrefix,
	parseList,
	shiftDay,
} from "../shared/termine";

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
