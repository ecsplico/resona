import { describe, it, expect, vi, beforeEach } from "vitest";
import type { Mock } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { pullHubToken, pushHubToken } from "~/utils/hubTokenSync";
import type { HubTokenRemote } from "~/utils/hubTokenSync";
import { useHubTokenSync } from "~/composables/useHubTokenSync";
import { useHubSettings } from "~/composables/useHubSettings";

function fakeRemote(stored: string | null | undefined): HubTokenRemote & { read: Mock; write: Mock } {
	return {
		read: vi.fn(async () => stored),
		write: vi.fn(async () => {}),
	};
}

describe("pullHubToken", () => {
	it("fills an empty local token from the account", async () => {
		const holder = { token: "" };
		const remote = fakeRemote("acct-token");
		expect(await pullHubToken(remote, holder)).toBe("filled");
		expect(holder.token).toBe("acct-token");
		expect(remote.write).not.toHaveBeenCalled();
	});

	it("treats a whitespace-only local token as empty", async () => {
		const holder = { token: "  " };
		expect(await pullHubToken(fakeRemote(" acct "), holder)).toBe("filled");
		expect(holder.token).toBe("acct");
	});

	it("keeps an existing local token and does not ask the account to overwrite it", async () => {
		const holder = { token: "local" };
		const remote = fakeRemote("other");
		expect(await pullHubToken(remote, holder)).toBe("unchanged");
		expect(holder.token).toBe("local");
		expect(remote.write).not.toHaveBeenCalled();
	});

	it("uploads the local token when the account has none yet", async () => {
		const holder = { token: "local" };
		for (const stored of [null, undefined, "", "  "]) {
			const remote = fakeRemote(stored);
			expect(await pullHubToken(remote, holder)).toBe("uploaded");
			expect(remote.write).toHaveBeenCalledWith("local");
		}
	});

	it("does nothing when neither side has a token", async () => {
		const holder = { token: "" };
		const remote = fakeRemote(null);
		expect(await pullHubToken(remote, holder)).toBe("unchanged");
		expect(holder.token).toBe("");
		expect(remote.write).not.toHaveBeenCalled();
	});

	it("propagates a failing read so the caller can ignore it", async () => {
		const remote = fakeRemote("x");
		remote.read.mockRejectedValueOnce(new Error("403"));
		await expect(pullHubToken(remote, { token: "" })).rejects.toThrow("403");
	});
});

describe("pushHubToken", () => {
	it("stores the trimmed token on the account", async () => {
		const remote = fakeRemote(null);
		await pushHubToken(remote, "  tok  ");
		expect(remote.write).toHaveBeenCalledWith("tok");
	});

	it("clears the account token for an empty input", async () => {
		const remote = fakeRemote("old");
		await pushHubToken(remote, "   ");
		expect(remote.write).toHaveBeenCalledWith(null);
	});
});

// ── composable over a mocked Directus client ───────────────────────────────
const request = vi.hoisted(() => vi.fn());
mockNuxtImport("useDirectus", () => () => ({ client: { request } }));

/** The REST command the SDK builds: `{ path, method, params | body }`. */
function command(index: number): { path: string; method?: string; body?: string; params?: Record<string, unknown> } {
	const cmd = request.mock.calls[index]![0] as () => { path: string; method?: string; body?: string; params?: Record<string, unknown> };
	return cmd();
}

describe("useHubTokenSync (directus mode)", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		useHubSettings().token = "";
	});

	it("pull reads /users/me and fills the empty device token", async () => {
		request.mockResolvedValueOnce({ hub_token: "from-account" });
		const result = await useHubTokenSync().pull();
		expect(result).toBe("filled");
		expect(useHubSettings().token).toBe("from-account");
		const read = command(0);
		expect(read.path).toBe("/users/me");
		expect(read.params).toMatchObject({ fields: ["hub_token"] });
	});

	it("pull uploads an existing device token via PATCH /users/me when the account has none", async () => {
		useHubSettings().token = "device-token";
		request.mockResolvedValueOnce({ hub_token: null }).mockResolvedValueOnce({});
		expect(await useHubTokenSync().pull()).toBe("uploaded");
		const write = command(1);
		expect(write.path).toBe("/users/me");
		expect(write.method).toBe("PATCH");
		expect(JSON.parse(write.body!)).toEqual({ hub_token: "device-token" });
	});

	it("push writes the entered token to the account", async () => {
		request.mockResolvedValueOnce({});
		expect(await useHubTokenSync().push("typed")).toBe(true);
		expect(command(0).method).toBe("PATCH");
		expect(JSON.parse(command(0).body!)).toEqual({ hub_token: "typed" });
	});

	it("push surfaces a Directus failure to the caller", async () => {
		request.mockRejectedValueOnce(new Error("forbidden"));
		await expect(useHubTokenSync().push("typed")).rejects.toThrow("forbidden");
	});
});
