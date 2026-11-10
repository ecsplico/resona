import { describe, it, expect } from "vitest";
import fixture from "#shared/titleSuggestion.cases.json";
import { placeholderTitle, suggestTitle } from "~/utils/titleSuggestion";

describe("suggestTitle", () => {
	it.each(fixture.cases)("$name", ({ text, title }) => {
		expect(suggestTitle(text)).toBe(title);
	});
});

describe("placeholderTitle", () => {
	it("formats local time zero-padded", () => {
		expect(placeholderTitle(new Date(2026, 10, 10, 9, 5))).toBe("Aufnahme 10.11.2026 09:05");
	});
});
