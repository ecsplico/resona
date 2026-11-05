import { describe, it, expect } from "vitest";
import { eventDisplayTitle, formatEventDate, formatEventWhen, germanDateToIso, isoDateToGerman } from "#shared/calendar";
import type { CalendarEvent } from "#shared/calendar";

function ev(partial: Partial<CalendarEvent>): CalendarEvent {
	return {
		id: "1",
		title: "Visite",
		start: "2026-06-27T09:30:00+02:00",
		end: "2026-06-27T10:00:00+02:00",
		allDay: false,
		...partial,
	};
}

describe("eventDisplayTitle", () => {
	it("combines event name and date as '<name> – <date>'", () => {
		expect(eventDisplayTitle(ev({}))).toBe("Visite – 27.06.2026");
	});

	it("falls back to 'Termin' when the title is empty", () => {
		expect(eventDisplayTitle(ev({ title: "  " }))).toBe("Termin – 27.06.2026");
	});

	it("drops the date when start is unparseable", () => {
		expect(eventDisplayTitle(ev({ title: "X", start: "not-a-date" }))).toBe("X");
	});
});

describe("formatEventDate", () => {
	it("formats an ISO datetime as dd.MM.yyyy", () => {
		expect(formatEventDate("2026-06-27T09:30:00+02:00")).toBe("27.06.2026");
	});

	it("returns empty string for invalid input", () => {
		expect(formatEventDate("nope")).toBe("");
	});
});

describe("isoDateToGerman", () => {
	it("converts ISO date to TT.MM.JJJJ", () => {
		expect(isoDateToGerman("2026-06-27")).toBe("27.06.2026");
	});

	it("returns empty string for blank or non-ISO input", () => {
		expect(isoDateToGerman("")).toBe("");
		expect(isoDateToGerman("27.06.2026")).toBe("");
	});
});

describe("germanDateToIso", () => {
	it("parses TT.MM.JJJJ to ISO", () => {
		expect(germanDateToIso("27.06.2026")).toBe("2026-06-27");
	});

	it("accepts single-digit day/month", () => {
		expect(germanDateToIso("3.7.2026")).toBe("2026-07-03");
	});

	it("rejects impossible and malformed dates", () => {
		expect(germanDateToIso("31.02.2026")).toBe("");
		expect(germanDateToIso("2026-06-27")).toBe("");
		expect(germanDateToIso("foo")).toBe("");
	});

	it("round-trips with isoDateToGerman", () => {
		expect(germanDateToIso(isoDateToGerman("2026-12-01"))).toBe("2026-12-01");
	});
});

describe("formatEventWhen", () => {
	it("labels all-day events", () => {
		const when = formatEventWhen(ev({ allDay: true, start: "2026-06-27" }));
		expect(when).toContain("27.06.2026");
		expect(when).toContain("ganztägig");
	});

	it("includes a time for timed events", () => {
		expect(formatEventWhen(ev({}))).toContain("27.06.2026");
	});
});
