import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { useMcpToken } from "~/composables/useMcpToken";
import { generateMcpToken, mcpSnippets, mcpUrl } from "~/utils/mcpSetup";

const request = vi.hoisted(() => vi.fn());
mockNuxtImport("useDirectus", () => () => ({ client: { request } }));

interface Command { path: string; method?: string; body?: string; params?: Record<string, unknown> }
/** The REST command the SDK builds for the n-th request. */
function command(index: number): Command {
	return (request.mock.calls[index]![0] as () => Command)();
}

const BASE64URL_32_BYTES = /^[\w-]{43}$/;

function decode(token: string): Uint8Array {
	const b64 = token.replace(/-/g, "+").replace(/_/g, "/").padEnd(44, "=");
	return Uint8Array.from(atob(b64), c => c.charCodeAt(0));
}

describe("generateMcpToken", () => {
	it("is 32 random bytes as base64url without padding", () => {
		const a = generateMcpToken();
		const b = generateMcpToken();
		expect(a).toMatch(BASE64URL_32_BYTES);
		expect(decode(a)).toHaveLength(32);
		expect(a).not.toBe(b);
	});

	it("draws the bytes from crypto.getRandomValues", () => {
		const spy = vi.spyOn(crypto, "getRandomValues");
		generateMcpToken();
		expect(spy).toHaveBeenCalledTimes(1);
		expect((spy.mock.calls[0]![0] as Uint8Array).byteLength).toBe(32);
		spy.mockRestore();
	});
});

describe("mcpSnippets", () => {
	const [code, desktop, cursor] = mcpSnippets("https://app.example/", "tok");

	it("points every client at <origin>/mcp with the token as Bearer", () => {
		expect(mcpUrl("https://app.example/")).toBe("https://app.example/mcp");
		expect(code!.text).toBe("claude mcp add --transport http resona https://app.example/mcp --header \"Authorization: Bearer tok\"");
		expect(JSON.parse(cursor!.text)).toEqual({
			mcpServers: { resona: { url: "https://app.example/mcp", headers: { Authorization: "Bearer tok" } } },
		});
		expect(JSON.parse(desktop!.text)).toEqual({
			mcpServers: {
				resona: {
					command: "npx",
					args: ["-y", "mcp-remote", "https://app.example/mcp", "--header", "Authorization:${RESONA_AUTH}"],
					env: { RESONA_AUTH: "Bearer tok" },
				},
			},
		});
	});
});

describe("useMcpToken (directus mode)", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("checks for a token with a filter that returns only the own id, never the token", async () => {
		request.mockResolvedValueOnce([{ id: "u1" }]);
		const mcp = useMcpToken();
		expect(mcp.availability).toBe("available");
		expect(await mcp.check()).toBe(true);
		expect(mcp.exists.value).toBe(true);
		expect(mcp.shown.value).toBeNull();
		const read = command(0);
		expect(read.path).toBe("/users");
		expect(read.method).toBe("GET");
		expect(read.params).toEqual({
			filter: { id: { _eq: "$CURRENT_USER" }, token: { _nnull: true } },
			fields: ["id"],
			limit: 1,
		});
	});

	it("reports no token when the filter matches nothing", async () => {
		request.mockResolvedValueOnce([]);
		const mcp = useMcpToken();
		await mcp.check();
		expect(mcp.exists.value).toBe(false);
	});

	it("creates a token, stores it via PATCH /users/me and shows it once", async () => {
		request.mockResolvedValueOnce([]).mockResolvedValueOnce({ id: "u1" });
		const mcp = useMcpToken();
		await mcp.check();
		expect(await mcp.create()).toBe(true);

		const write = command(1);
		expect(write.path).toBe("/users/me");
		expect(write.method).toBe("PATCH");
		// the response carries only the id, never the token
		expect(write.params).toEqual({ fields: ["id"] });
		const { token } = JSON.parse(write.body!) as { token: string };
		expect(token).toMatch(BASE64URL_32_BYTES);
		expect(mcp.shown.value).toBe(token);
		expect(mcp.exists.value).toBe(true);
		expect(mcp.busy.value).toBe(false);

		mcp.hide();
		expect(mcp.shown.value).toBeNull();
		expect(mcp.exists.value).toBe(true);
	});

	it("does not show the token again: a new instance only learns that one exists", async () => {
		request.mockResolvedValueOnce({ id: "u1" }).mockResolvedValueOnce([{ id: "u1" }]);
		const first = useMcpToken();
		await first.create();
		expect(first.shown.value).toMatch(BASE64URL_32_BYTES);

		const later = useMcpToken();
		await later.check();
		expect(later.exists.value).toBe(true);
		expect(later.shown.value).toBeNull();
		for (const [cmd] of request.mock.calls) {
			const { params } = (cmd as () => Command)();
			expect(JSON.stringify(params?.fields ?? [])).not.toContain("token");
		}
	});

	it("replaces an existing token with a new one", async () => {
		request.mockResolvedValueOnce([{ id: "u1" }]).mockResolvedValue({ id: "u1" });
		const mcp = useMcpToken();
		await mcp.check();
		await mcp.create();
		const firstToken = mcp.shown.value;
		await mcp.create();
		const second = JSON.parse(command(2).body!) as { token: string };
		expect(command(2).path).toBe("/users/me");
		expect(second.token).not.toBe(firstToken);
		expect(mcp.shown.value).toBe(second.token);
		expect(mcp.exists.value).toBe(true);
	});

	it("revokes: clears the account token and hides a shown one", async () => {
		request.mockResolvedValue({ id: "u1" });
		const mcp = useMcpToken();
		await mcp.create();
		expect(await mcp.revoke()).toBe(true);
		const write = command(1);
		expect(write.method).toBe("PATCH");
		expect(write.path).toBe("/users/me");
		expect(JSON.parse(write.body!)).toEqual({ token: null });
		expect(write.params).toEqual({ fields: ["id"] });
		expect(mcp.exists.value).toBe(false);
		expect(mcp.shown.value).toBeNull();
	});

	it("shows no token and reports the failure when storing it fails", async () => {
		request.mockResolvedValueOnce([]).mockRejectedValueOnce(new Error("403"));
		const mcp = useMcpToken();
		await mcp.check();
		expect(await mcp.create()).toBe(false);
		expect(mcp.shown.value).toBeNull();
		expect(mcp.exists.value).toBe(false);
		expect(mcp.error.value).toContain("nicht gespeichert");
	});

	it("keeps the state when revoking fails", async () => {
		request.mockResolvedValueOnce([{ id: "u1" }]).mockRejectedValueOnce(new Error("offline"));
		const mcp = useMcpToken();
		await mcp.check();
		expect(await mcp.revoke()).toBe(false);
		expect(mcp.exists.value).toBe(true);
		expect(mcp.error.value).toContain("nicht widerrufen");
	});

	it("leaves existence unknown when the check fails", async () => {
		request.mockRejectedValueOnce(new Error("offline"));
		const mcp = useMcpToken();
		expect(await mcp.check()).toBe(false);
		expect(mcp.exists.value).toBeNull();
		expect(mcp.error.value).toContain("nicht geprüft");
	});
});

// Real runtimeConfig, data mode set and restored per test (see useDataStore.spec.ts).
describe("useMcpToken (standalone mode)", () => {
	let original: string;

	beforeEach(() => {
		vi.clearAllMocks();
		original = useRuntimeConfig().public.dataMode;
		useRuntimeConfig().public.dataMode = "standalone";
	});

	afterEach(() => {
		useRuntimeConfig().public.dataMode = original;
	});

	it("is unavailable and never touches Directus", async () => {
		const mcp = useMcpToken();
		expect(mcp.availability).toBe("standalone");
		expect(await mcp.check()).toBe(false);
		expect(await mcp.create()).toBe(false);
		expect(await mcp.revoke()).toBe(false);
		expect(request).not.toHaveBeenCalled();
		expect(mcp.shown.value).toBeNull();
	});
});
