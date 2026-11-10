import { describe, it, expect } from "vitest";
import fixture from "#shared/titleSuggestion.cases.json";
import {
	deriveTitle,
	fallbackTitle,
	meaningfulWordCount,
	placeholderTitle,
	suggestTitle,
	TITLE_MAX_CHARS,
} from "~/utils/titleSuggestion";

const date = new Date(2026, 10, 10, 9, 5);

// The rule itself: the same cases run against the worker's Python port.
describe("deriveTitle (shared cases)", () => {
	it.each(fixture.cases)("$name", ({ text, title }) => {
		expect(deriveTitle(text)).toBe(title);
	});

	it("never exceeds the length limit", () => {
		for (const { text } of fixture.cases) {
			expect((deriveTitle(text) ?? "").length).toBeLessThanOrEqual(TITLE_MAX_CHARS);
		}
	});
});

describe("suggestTitle", () => {
	it("uses the shared rule when there is no preset", () => {
		expect(suggestTitle("Befund unauffällig. Keine Auffälligkeiten.", { date })).toBe("Befund unauffällig");
	});

	it("falls back to date and time for empty or filler-only text", () => {
		expect(suggestTitle("", { date })).toBe("Diktat 10.11.2026 09:05");
		expect(suggestTitle("  äh ähm hm Komma Punkt Absatz ", { date })).toBe("Diktat 10.11.2026 09:05");
		expect(fallbackTitle(new Date(2026, 0, 3, 14, 30))).toBe("Diktat 03.01.2026 14:30");
	});

	it("prefers a calendar title over everything else", () => {
		expect(suggestTitle("## Befund\n\nText", { calendarTitle: " Visite – 10.11.2026 ", date })).toBe("Visite – 10.11.2026");
		expect(suggestTitle("", { calendarTitle: "Visite" })).toBe("Visite");
		expect(suggestTitle("Patient stabil.", { calendarTitle: "  ", date })).toBe("Patient stabil");
	});
});

describe("placeholderTitle", () => {
	it("formats local time zero-padded", () => {
		expect(placeholderTitle(date)).toBe("Aufnahme 10.11.2026 09:05");
	});
});

describe("meaningfulWordCount", () => {
	it("ignores fillers and dictation commands", () => {
		expect(meaningfulWordCount("äh der Patient Komma ähm stabil Punkt")).toBe(3);
		expect(meaningfulWordCount("")).toBe(0);
	});
});
