import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useProfiles } from "~/composables/useProfiles";

// resonaApiUrl ("http://localhost:7710") + defaultProfile ("default") come from
// the real nuxt.config runtimeConfig. $fetch is a Nuxt global — stub it directly.
const fetchMock = vi.fn();
const realFetch = (globalThis as Record<string, unknown>).$fetch;

describe("useProfiles", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		(globalThis as Record<string, unknown>).$fetch = fetchMock;
	});

	afterEach(() => {
		(globalThis as Record<string, unknown>).$fetch = realFetch;
	});

	it("GETs resona-api /profiles/ and returns the profile names (array of strings)", async () => {
		fetchMock.mockResolvedValue(["default", "medical"]);
		const p = useProfiles();
		const names = await p.list();
		expect(fetchMock).toHaveBeenCalledWith("http://localhost:7710/profiles/");
		expect(names).toEqual(["default", "medical"]);
	});

	it("maps an array of objects with a name field to string names", async () => {
		fetchMock.mockResolvedValue([{ name: "default" }, { name: "medical" }]);
		const p = useProfiles();
		const names = await p.list();
		expect(names).toEqual(["default", "medical"]);
	});

	it("falls back to [defaultProfile] on error", async () => {
		fetchMock.mockRejectedValue(new Error("offline"));
		const p = useProfiles();
		const names = await p.list();
		expect(names).toEqual(["default"]);
	});
});
