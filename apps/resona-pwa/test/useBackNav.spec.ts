import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { previousPath, useBackNav } from "~/composables/useBackNav";

const router = vi.hoisted(() => ({
	options: { history: { state: { back: null as string | null } } },
	back: vi.fn(),
	// Nuxt plugins touch the router while the test app boots.
	beforeEach: vi.fn(() => () => {}),
	afterEach: vi.fn(() => () => {}),
	beforeResolve: vi.fn(() => () => {}),
	onError: vi.fn(() => () => {}),
}));
mockNuxtImport("useRouter", () => () => router);

const navigateTo = vi.hoisted(() => vi.fn());
mockNuxtImport("navigateTo", () => navigateTo);

describe("previousPath", () => {
	it("is the entry before the current one", () => {
		expect(previousPath({ options: { history: { state: { back: "/documents" } } } } as never)).toBe("/documents");
	});

	it("is null for a direct entry (no earlier in-app entry)", () => {
		expect(previousPath({ options: { history: { state: { back: null } } } } as never)).toBeNull();
	});

	it("ignores transient entries (login, share target)", () => {
		expect(previousPath({ options: { history: { state: { back: "/login" } } } } as never)).toBeNull();
		expect(previousPath({ options: { history: { state: { back: "/share-target?kind=audio" } } } } as never)).toBeNull();
	});
});

describe("useBackNav", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		router.options.history.state.back = null;
	});

	it("back() pops the history when the app has an earlier entry", async () => {
		router.options.history.state.back = "/documents";
		await useBackNav().back("/");
		expect(router.back).toHaveBeenCalledTimes(1);
		expect(navigateTo).not.toHaveBeenCalled();
	});

	it("back() replaces with the fallback list when entered directly", async () => {
		await useBackNav().back("/documents");
		expect(router.back).not.toHaveBeenCalled();
		expect(navigateTo).toHaveBeenCalledWith("/documents", { replace: true });
	});

	it("leaveTo() pops when the previous entry already is the target (list keeps scroll and search)", async () => {
		router.options.history.state.back = "/documents";
		await useBackNav().leaveTo("/documents");
		expect(router.back).toHaveBeenCalledTimes(1);
		expect(navigateTo).not.toHaveBeenCalled();
	});

	it("leaveTo() replaces when the previous entry is somewhere else", async () => {
		router.options.history.state.back = "/calendar";
		await useBackNav().leaveTo("/");
		expect(router.back).not.toHaveBeenCalled();
		expect(navigateTo).toHaveBeenCalledWith("/", { replace: true });
	});

	it("leaveTo() replaces when entered directly", async () => {
		await useBackNav().leaveTo("/");
		expect(navigateTo).toHaveBeenCalledWith("/", { replace: true });
	});
});
