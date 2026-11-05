import { describe, it, expect } from "vitest";
import { isCalendarEnabled } from "#shared/calendarEnabled";
import { assertCalendarEnabled } from "~~/server/utils/calendar/enabled";

describe("isCalendarEnabled", () => {
	it.each([
		[true, true],
		["true", true],
		[false, false],
		["false", false],
		[undefined, false],
		["", false],
		[1, false],
	])("%j → %s", (value, expected) => {
		expect(isCalendarEnabled(value)).toBe(expected);
	});
});

describe("assertCalendarEnabled", () => {
	it("throws a 404 when off", () => {
		expect(() => assertCalendarEnabled(false)).toThrow(expect.objectContaining({ statusCode: 404 }));
	});

	it("passes when on", () => {
		expect(() => assertCalendarEnabled(true)).not.toThrow();
		expect(() => assertCalendarEnabled("true")).not.toThrow();
	});
});
