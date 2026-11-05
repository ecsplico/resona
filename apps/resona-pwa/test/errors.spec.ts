import { describe, it, expect } from "vitest";
import { isServiceUnreachable } from "#shared/errors";

describe("isServiceUnreachable", () => {
	it("flags the worker's unreachable message", () => {
		expect(isServiceUnreachable("Transkriptionsdienst nicht erreichbar (http://api). Läuft der Backend-Dienst?")).toBe(true);
	});

	it("flags a timeout message", () => {
		expect(isServiceUnreachable("Zeitüberschreitung beim Transkriptionsdienst (http://api).")).toBe(true);
	});

	it("does not flag a genuine processing error", () => {
		expect(isServiceUnreachable("Transkription fehlgeschlagen (HTTP 400): no engine available")).toBe(false);
	});

	it("handles null/empty", () => {
		expect(isServiceUnreachable(null)).toBe(false);
		expect(isServiceUnreachable("")).toBe(false);
		expect(isServiceUnreachable(undefined)).toBe(false);
	});
});
