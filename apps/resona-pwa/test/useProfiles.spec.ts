import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useProfiles } from "~/composables/useProfiles";
import { useHubSettings } from "~/composables/useHubSettings";

// resonaApiUrl ("http://localhost:7710") + defaultProfile ("default") come from
// the real nuxt.config runtimeConfig; the hub token from useHubSettings.
const fetchMock = vi.fn();
const realFetch = globalThis.fetch;

function jsonResponse(body: unknown, status = 200): Response {
	return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("useProfiles", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		globalThis.fetch = fetchMock as unknown as typeof fetch;
		useHubSettings().token = "tok";
	});

	afterEach(() => {
		globalThis.fetch = realFetch;
		useHubSettings().token = "";
	});

	it("GETs the hub /profiles with Bearer auth and returns the profile names", async () => {
		fetchMock.mockResolvedValue(jsonResponse({ profiles: [{ name: "default", revision: 1 }, { name: "medical", revision: 3 }] }));
		const p = useProfiles();
		const names = await p.list();
		expect(fetchMock).toHaveBeenCalledWith(
			"http://localhost:7710/profiles",
			expect.objectContaining({ headers: expect.objectContaining({ Authorization: "Bearer tok" }) }),
		);
		expect(names).toEqual(["default", "medical"]);
		expect(p.profiles.value).toEqual(["default", "medical"]);
	});

	it("falls back to [defaultProfile] on an HTTP error", async () => {
		fetchMock.mockResolvedValue(jsonResponse({ error: "unauthorized", message: "bad token" }, 401));
		const p = useProfiles();
		expect(await p.list()).toEqual(["default"]);
	});

	it("falls back to [defaultProfile] on a network error", async () => {
		fetchMock.mockRejectedValue(new TypeError("offline"));
		const p = useProfiles();
		expect(await p.list()).toEqual(["default"]);
	});
});
