import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import type { AuthenticationData, AuthenticationStorage } from "@directus/sdk";
import { useDirectus } from "~/composables/useDirectus";
import { AUTH_STORAGE_KEY, preferencesAuthStorage } from "~/utils/directusAuth";

// Inside the Capacitor app: JSON auth mode, tokens in Capacitor Preferences.
vi.mock("@capacitor/core", () => ({
	Capacitor: { isNativePlatform: () => true, isPluginAvailable: () => false, getPlatform: () => "android" },
}));

// Preferences as the native store: a Map that outlives every storage instance,
// like SharedPreferences outlive an app restart.
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

// useDirectus wraps client.request on construction, so keep the raw fn apart.
const rawRequest = vi.hoisted(() => vi.fn());
const mockClient = vi.hoisted(() => ({
	login: vi.fn(),
	logout: vi.fn(),
	getToken: vi.fn(),
	refresh: vi.fn(),
	stopRefreshing: vi.fn(),
	request: rawRequest as (...args: unknown[]) => unknown,
}));
const navigateTo = vi.hoisted(() => vi.fn());
mockNuxtImport("navigateTo", () => navigateTo);
const authConfig = vi.hoisted(() => ({ mode: "", options: undefined as unknown }));
vi.mock("@directus/sdk", () => ({
	createDirectus: vi.fn(() => ({ with: () => ({ with: () => mockClient }) })),
	authentication: vi.fn((mode: string, options: unknown) => {
		authConfig.mode = mode;
		authConfig.options = options;
		return (c: unknown) => c;
	}),
	rest: vi.fn(() => (c: unknown) => c),
	readItems: vi.fn(),
	readItem: vi.fn(),
	createItem: vi.fn(),
	updateItem: vi.fn(),
	deleteItem: vi.fn(),
	uploadFiles: vi.fn(),
	readMe: vi.fn(() => "readMe"),
	updateMe: vi.fn((data: unknown) => ({ updateMe: data })),
}));

interface AuthOpts {
	autoRefresh?: boolean;
	credentials?: string;
	storage: AuthenticationStorage;
}
const options = () => authConfig.options as AuthOpts;
const session = (over: Partial<AuthenticationData> = {}): AuthenticationData => ({
	access_token: "acc",
	refresh_token: "ref",
	expires: 900_000,
	expires_at: Date.now() + 900_000,
	...over,
});
const stored = () => {
	const raw = prefs.get(AUTH_STORAGE_KEY);
	return raw ? JSON.parse(raw) as AuthenticationData : null;
};

describe("useDirectus in the Capacitor app", () => {
	beforeEach(async () => {
		vi.clearAllMocks();
		mockClient.logout.mockResolvedValue(undefined);
		rawRequest.mockResolvedValue({});
		await useDirectus().logout();
		prefs.clear();
		localStorage.clear();
	});

	it("uses JSON auth without the refresh timer or cookies, and no SSO", () => {
		const d = useDirectus();
		expect(authConfig.mode).toBe("json");
		expect(options().autoRefresh).toBe(false);
		expect(options()).not.toHaveProperty("credentials");
		expect(d.ssoSupported).toBe(false);
	});

	it("keeps access and refresh token in Preferences, never in localStorage", async () => {
		useDirectus();
		await options().storage.set(session());
		expect(stored()).toMatchObject({ access_token: "acc", refresh_token: "ref" });
		expect(localStorage.length).toBe(0);
	});

	it("a restarted app reads the session back from Preferences", async () => {
		await preferencesAuthStorage().set(session({ refresh_token: "persisted" }));
		// A new instance has no memory copy: it must come from the native store.
		expect(await preferencesAuthStorage().get()).toMatchObject({ refresh_token: "persisted" });
	});

	it("restoreSession refreshes an expired stored session (app back after > 15 min) and stays logged in", async () => {
		const d = useDirectus();
		await options().storage.set(session({ access_token: "old", expires_at: Date.now() - 60_000 }));
		mockClient.refresh.mockResolvedValue({ access_token: "new" });
		mockClient.getToken.mockResolvedValue("new");

		expect(await d.restoreSession()).toBe(true);
		expect(mockClient.refresh).toHaveBeenCalledTimes(1);
		expect(d.isAuthenticated.value).toBe(true);
	});

	it("restoreSession clears the stored tokens when the refresh token is rejected", async () => {
		const d = useDirectus();
		await options().storage.set(session({ expires_at: Date.now() - 60_000 }));
		mockClient.refresh.mockRejectedValue(new Error("Invalid user credentials."));

		expect(await d.restoreSession()).toBe(false);
		expect(stored()).toBeNull();
		expect(await options().storage.get()).toBeNull();
	});

	it("logout removes the tokens from Preferences, also when the logout request fails", async () => {
		const d = useDirectus();
		mockClient.login.mockResolvedValue(session());
		mockClient.getToken.mockResolvedValue("acc");
		await d.login("a@b.de", "pw");
		await options().storage.set(session());
		mockClient.logout.mockRejectedValue(new Error("offline"));

		await d.logout();
		expect(d.isAuthenticated.value).toBe(false);
		expect(stored()).toBeNull();
		expect(await options().storage.get()).toBeNull();
	});

	it("asset URLs carry the refreshed access token, not the login-time one", async () => {
		const d = useDirectus();
		mockClient.login.mockResolvedValue(session());
		mockClient.getToken.mockResolvedValue("acc");
		await d.login("a@b.de", "pw");
		expect(d.assetUrl("f")).toBe("http://localhost:7700/assets/f?access_token=acc");

		// What the SDK writes on a refresh: a token-less session, then the new one.
		await options().storage.set({ access_token: null, refresh_token: null, expires: null, expires_at: null });
		await options().storage.set(session({ access_token: "fresh", refresh_token: "ref2" }));
		expect(d.assetUrl("f")).toBe("http://localhost:7700/assets/f?access_token=fresh");
		expect(stored()).toMatchObject({ refresh_token: "ref2" });
	});
});

describe("preferencesAuthStorage", () => {
	beforeEach(() => prefs.clear());

	it("removes the key for a token-less session (SDK logout) and for null", async () => {
		const s = preferencesAuthStorage();
		await s.set(session());
		await s.set({ access_token: null, refresh_token: null, expires: null, expires_at: null });
		expect(prefs.has(AUTH_STORAGE_KEY)).toBe(false);
		expect(await s.get()).toBeNull();

		await s.set(session());
		await s.set(null);
		expect(prefs.has(AUTH_STORAGE_KEY)).toBe(false);
	});

	it("a write during the first native read wins over the value read", async () => {
		prefs.set(AUTH_STORAGE_KEY, JSON.stringify(session({ access_token: "old" })));
		const s = preferencesAuthStorage();
		const reading = s.get();
		await s.set(null);
		await reading;
		expect(await s.get()).toBeNull();
	});
});
