import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { useDirectus } from "~/composables/useDirectus";

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
// Capture how authentication() was configured at client-construction time. The
// client is a module singleton built on first use, so we stash the args here
// (clearAllMocks wipes mock.calls but not this object).
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
}));

interface AuthStorageOpts {
	autoRefresh?: boolean;
	storage?: {
		get(): unknown;
		set(value: unknown): void;
	};
}

describe("useDirectus", () => {
	beforeEach(async () => {
		vi.clearAllMocks();
		// reset shared auth state between tests (singleton refs bleed otherwise)
		mockClient.logout.mockResolvedValue(undefined);
		await useDirectus().logout();
	});

	it("configures JSON auth with autoRefresh and a localStorage-backed storage", () => {
		useDirectus();
		expect(authConfig.mode).toBe("json");
		const opts = authConfig.options as AuthStorageOpts;
		expect(opts.autoRefresh).toBe(true);
		expect(opts.storage).toBeTruthy();
	});

	it("the auth storage round-trips the session through localStorage", () => {
		useDirectus();
		const storage = (authConfig.options as AuthStorageOpts).storage!;
		localStorage.clear();

		const session = { access_token: "a", refresh_token: "r", expires: 1, expires_at: 2 };
		storage.set(session);
		// Persisted under a key, so a fresh read after a "reload" sees it.
		expect(storage.get()).toEqual(session);
		expect(localStorage.length).toBe(1);

		storage.set(null);
		expect(storage.get()).toBeNull();
		expect(localStorage.length).toBe(0);
	});

	it("exposes the singleton client", () => {
		const a = useDirectus();
		const b = useDirectus();
		expect(a.client).toBe(b.client);
		expect(a.isAuthenticated).toBe(b.isAuthenticated);
	});

	it("login calls client.login with credentials and sets isAuthenticated", async () => {
		mockClient.login.mockResolvedValue({ access_token: "t" });
		const d = useDirectus();
		await d.login("a@b.de", "pw");
		expect(mockClient.login).toHaveBeenCalledWith({ email: "a@b.de", password: "pw" });
		expect(d.isAuthenticated.value).toBe(true);
	});

	it("logout clears isAuthenticated", async () => {
		mockClient.logout.mockResolvedValue(undefined);
		const d = useDirectus();
		d.isAuthenticated.value = true;
		await d.logout();
		expect(mockClient.logout).toHaveBeenCalled();
		expect(d.isAuthenticated.value).toBe(false);
	});

	it("logout clears isAuthenticated even when client.logout rejects", async () => {
		mockClient.logout.mockRejectedValue(new Error("nope"));
		const d = useDirectus();
		d.isAuthenticated.value = true;
		await d.logout();
		expect(d.isAuthenticated.value).toBe(false);
	});

	it("restoreSession returns true when getToken resolves a token", async () => {
		mockClient.getToken.mockResolvedValue("tok");
		const d = useDirectus();
		const ok = await d.restoreSession();
		expect(ok).toBe(true);
		expect(d.isAuthenticated.value).toBe(true);
	});

	it("restoreSession returns false when getToken resolves null", async () => {
		mockClient.getToken.mockResolvedValue(null);
		const d = useDirectus();
		const ok = await d.restoreSession();
		expect(ok).toBe(false);
		expect(d.isAuthenticated.value).toBe(false);
	});

	it("restoreSession returns false when getToken throws", async () => {
		mockClient.getToken.mockRejectedValue(new Error("boom"));
		const d = useDirectus();
		const ok = await d.restoreSession();
		expect(ok).toBe(false);
		expect(d.isAuthenticated.value).toBe(false);
	});

	it("assetUrl returns a token-less URL before auth", () => {
		const d = useDirectus();
		expect(d.assetUrl("abc-123")).toBe("http://localhost:7700/assets/abc-123");
	});

	describe("expired session", () => {
		const storageOf = () => (authConfig.options as AuthStorageOpts).storage!;
		const expiredSession = () => ({ access_token: "old", refresh_token: "r", expires: 900000, expires_at: Date.now() - 1000 });

		it("restoreSession returns false and clears the stored session when the refresh fails", async () => {
			useDirectus();
			storageOf().set(expiredSession());
			mockClient.refresh.mockRejectedValue(new Error("Invalid user credentials."));
			mockClient.getToken.mockResolvedValue("old");
			const d = useDirectus();

			expect(await d.restoreSession()).toBe(false);
			expect(d.isAuthenticated.value).toBe(false);
			expect(storageOf().get()).toBeNull();
		});

		it("restoreSession refreshes an expired session and stays logged in", async () => {
			useDirectus();
			storageOf().set(expiredSession());
			mockClient.refresh.mockResolvedValue({ access_token: "new" });
			mockClient.getToken.mockResolvedValue("new");
			const d = useDirectus();

			expect(await d.restoreSession()).toBe(true);
			expect(mockClient.refresh).toHaveBeenCalledTimes(1);
			expect(d.assetUrl("f")).toBe("http://localhost:7700/assets/f?access_token=new");
		});

		it("a 401 from a request logs out locally and navigates to /login, once", async () => {
			const d = useDirectus();
			storageOf().set({ ...expiredSession(), expires_at: Date.now() + 600000 });
			d.isAuthenticated.value = true;
			const err = Object.assign(new Error("Token expired."), { response: { status: 401 } });
			rawRequest.mockRejectedValue(err);

			await expect(d.client.request({} as never)).rejects.toBe(err);
			await expect(d.client.request({} as never)).rejects.toBe(err);

			expect(d.isAuthenticated.value).toBe(false);
			expect(storageOf().get()).toBeNull();
			expect(navigateTo).toHaveBeenCalledTimes(1);
			expect(navigateTo).toHaveBeenCalledWith("/login");
		});

		it("a non-auth request error leaves the session alone", async () => {
			const d = useDirectus();
			d.isAuthenticated.value = true;
			rawRequest.mockRejectedValue(Object.assign(new Error("Forbidden"), { response: { status: 403 } }));

			await expect(d.client.request({} as never)).rejects.toThrow("Forbidden");
			expect(d.isAuthenticated.value).toBe(true);
			expect(navigateTo).not.toHaveBeenCalled();
		});
	});

	it("assetUrl appends the access token after restoreSession", async () => {
		mockClient.getToken.mockResolvedValue("t");
		const d = useDirectus();
		await d.restoreSession();
		expect(d.assetUrl("file-1")).toBe("http://localhost:7700/assets/file-1?access_token=t");
	});

	it("assetUrl appends the access token after login", async () => {
		mockClient.login.mockResolvedValue({ access_token: "t" });
		mockClient.getToken.mockResolvedValue("t");
		const d = useDirectus();
		await d.login("a@b.de", "pw");
		expect(d.assetUrl("file-1")).toBe("http://localhost:7700/assets/file-1?access_token=t");
	});
});
