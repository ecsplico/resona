import { describe, it, expect, vi, beforeEach } from "vitest";
import { useDirectus } from "~/composables/useDirectus";

const mockClient = vi.hoisted(() => ({
	login: vi.fn(),
	logout: vi.fn(),
	getToken: vi.fn(),
	request: vi.fn(),
}));
vi.mock("@directus/sdk", () => ({
	createDirectus: vi.fn(() => ({ with: () => ({ with: () => mockClient }) })),
	authentication: vi.fn(() => (c: unknown) => c),
	rest: vi.fn(() => (c: unknown) => c),
	readItems: vi.fn(),
	readItem: vi.fn(),
	createItem: vi.fn(),
	updateItem: vi.fn(),
	deleteItem: vi.fn(),
	uploadFiles: vi.fn(),
}));

describe("useDirectus", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		// reset shared auth state between tests (singleton ref bleeds otherwise)
		useDirectus().isAuthenticated.value = false;
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

	it("assetUrl builds the right URL", () => {
		const d = useDirectus();
		expect(d.assetUrl("abc-123")).toBe("http://localhost:7700/assets/abc-123");
	});
});
