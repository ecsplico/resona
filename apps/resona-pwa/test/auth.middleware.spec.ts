import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import type { RouteLocationNormalized } from "vue-router";
import middleware from "~/middleware/auth.global";

const state = vi.hoisted(() => ({
	isAuthenticated: { value: false },
	restoreSession: vi.fn(async () => false),
}));

mockNuxtImport("useDirectus", () => () => state);

const navigateTo = vi.hoisted(() => vi.fn((to: string) => ({ __redirect: to })));
mockNuxtImport("navigateTo", () => navigateTo);

function route(path: string): RouteLocationNormalized {
	return { path } as RouteLocationNormalized;
}

const from = route("/");

describe("auth.global middleware", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		state.isAuthenticated.value = false;
		state.restoreSession.mockResolvedValue(false);
	});

	it("redirects unauthenticated users to /login", async () => {
		const result = await middleware(route("/"), from);
		expect(navigateTo).toHaveBeenCalledWith("/login");
		expect(result).toEqual({ __redirect: "/login" });
	});

	it("lets authenticated users through (no redirect)", async () => {
		state.isAuthenticated.value = true;
		const result = await middleware(route("/"), from);
		expect(navigateTo).not.toHaveBeenCalled();
		expect(result).toBeUndefined();
	});

	it("allows the /login route through while unauthenticated", async () => {
		const result = await middleware(route("/login"), from);
		expect(navigateTo).not.toHaveBeenCalled();
		expect(result).toBeUndefined();
	});

	it("calls restoreSession on the client before bouncing", async () => {
		state.restoreSession.mockImplementation(async () => {
			state.isAuthenticated.value = true;
			return true;
		});
		const result = await middleware(route("/"), from);
		expect(state.restoreSession).toHaveBeenCalled();
		expect(navigateTo).not.toHaveBeenCalled();
		expect(result).toBeUndefined();
	});
});

describe("auth.global middleware in standalone mode", () => {
	let cfg: { dataMode: string };
	let original: string;

	beforeEach(() => {
		vi.clearAllMocks();
		state.isAuthenticated.value = false;
		cfg = useRuntimeConfig().public as { dataMode: string };
		original = cfg.dataMode;
		cfg.dataMode = "standalone";
	});

	afterEach(() => {
		cfg.dataMode = original;
	});

	it("needs no login: lets unauthenticated users through without restoring a session", async () => {
		const result = await middleware(route("/"), from);
		expect(result).toBeUndefined();
		expect(navigateTo).not.toHaveBeenCalled();
		expect(state.restoreSession).not.toHaveBeenCalled();
	});

	it("redirects /login to /", async () => {
		const result = await middleware(route("/login"), from);
		expect(navigateTo).toHaveBeenCalledWith("/");
		expect(result).toEqual({ __redirect: "/" });
	});
});
