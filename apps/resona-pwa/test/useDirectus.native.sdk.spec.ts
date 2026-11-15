import { describe, it, expect, vi } from "vitest";
import { readItems } from "@directus/sdk";
import type * as DirectusSdk from "@directus/sdk";
import type { AuthenticationStorage } from "@directus/sdk";
import { useDirectus } from "~/composables/useDirectus";
import { AUTH_STORAGE_KEY } from "~/utils/directusAuth";

// The real SDK (authentication("json") + rest()) against a mocked fetch, inside the
// Capacitor app: what goes over the wire and what lands in Preferences.
vi.mock("@capacitor/core", () => ({
	Capacitor: { isNativePlatform: () => true, isPluginAvailable: () => false, getPlatform: () => "android" },
}));

const prefs = vi.hoisted(() => new Map<string, string>());
vi.mock("@capacitor/preferences", () => ({
	Preferences: {
		get: vi.fn(async ({ key }: { key: string }) => ({ value: prefs.get(key) ?? null })),
		set: vi.fn(async ({ key, value }: { key: string; value: string }) => {
			prefs.set(key, value);
		}),
		remove: vi.fn(async ({ key }: { key: string }) => {
			prefs.delete(key);
		}),
	},
}));

// Pass-through: the real authentication(), only its storage is kept for the test
// (the client is built during app init, before this file can seed Preferences).
const captured = vi.hoisted(() => ({ storage: null as AuthenticationStorage | null }));
vi.mock("@directus/sdk", async (importOriginal) => {
	const sdk = await importOriginal<typeof DirectusSdk>();
	return {
		...sdk,
		authentication: ((mode, options) => {
			captured.storage = options?.storage ?? null;
			return sdk.authentication(mode, options);
		}) as typeof sdk.authentication,
	};
});

const json = (body: unknown) =>
	new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

describe("useDirectus in the app with the real SDK", () => {
	it("two concurrent requests on an expired session refresh once with the stored refresh token and persist the rotated one", async () => {
		const { client } = useDirectus();
		// Last used > 15 min ago: the stored access token is expired.
		await captured.storage!.set({
			access_token: "a1",
			refresh_token: "r1",
			expires: 900_000,
			expires_at: Date.now() - 60_000,
		});
		const calls: { url: string; init: RequestInit }[] = [];
		const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
			calls.push({ url, init });
			if (url.endsWith("/auth/refresh")) {
				// Still in flight when the second request asks for a token.
				const { promise, resolve } = Promise.withResolvers<undefined>();
				setTimeout(resolve, 10, undefined);
				await promise;
				return json({ data: { access_token: "a2", refresh_token: "r2", expires: 900_000 } });
			}
			return json({ data: [] });
		});

		// The SDK reads globals.fetch per call; the client may predate this test.
		const realFetch = client.globals.fetch;
		client.globals.fetch = fetchMock as unknown as typeof fetch;
		await Promise.all([
			client.request(readItems("recordings")),
			client.request(readItems("transcripts")),
		]);

		const refreshes = calls.filter(c => c.url.endsWith("/auth/refresh"));
		expect(refreshes).toHaveLength(1);
		expect(JSON.parse(refreshes[0]!.init.body as string)).toEqual({ mode: "json", refresh_token: "r1" });
		expect(refreshes[0]!.init).not.toHaveProperty("credentials");

		const items = calls.filter(c => c.url.includes("/items/"));
		expect(items).toHaveLength(2);
		for (const c of items) expect((c.init.headers as Record<string, string>).Authorization).toBe("Bearer a2");

		expect(JSON.parse(prefs.get(AUTH_STORAGE_KEY)!)).toMatchObject({ access_token: "a2", refresh_token: "r2" });
		client.globals.fetch = realFetch;
	});
});
