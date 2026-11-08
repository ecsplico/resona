import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { computed, reactive, ref } from "vue";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import AppHeader from "~/components/AppHeader.vue";
import { useSettingsDrawer } from "~/composables/useShell";

const route = vi.hoisted(() => ({ path: "/" }));
mockNuxtImport("useRoute", () => () => reactive(route));

// `wide` normally comes from the layout's media query; drive it directly here.
const viewport = vi.hoisted(() => ({ wide: false }));
mockNuxtImport("useShell", () => () => ({ wide: ref(viewport.wide), paneInLayout: computed(() => false) }));

// The header starts the shared health poll on mount; keep it inert.
mockNuxtImport("useServerHealth", () => () => ({
	overall: { value: "up" },
	online: { value: true },
	resonaApi: { value: true },
	directus: { value: true },
	start: vi.fn(),
	check: vi.fn(),
}));

describe("AppHeader", () => {
	let original: unknown;

	beforeEach(() => {
		route.path = "/";
		viewport.wide = false;
		original = useRuntimeConfig().public.calendarEnabled;
	});

	afterEach(() => {
		useRuntimeConfig().public.calendarEnabled = original as boolean;
	});

	it("hides the Kalender tab while the calendar flag is off", async () => {
		useRuntimeConfig().public.calendarEnabled = false;
		const w = await mountSuspended(AppHeader);
		expect(w.find("[data-testid=\"nav-documents\"]").exists()).toBe(true);
		expect(w.find("[data-testid=\"nav-calendar\"]").exists()).toBe(false);
	});

	it("shows Aufnahmen, Dokumente and Kalender in that order when the flag is on", async () => {
		useRuntimeConfig().public.calendarEnabled = "true" as unknown as boolean;
		const w = await mountSuspended(AppHeader);
		const tabs = w.findAll(".h-tab");
		expect(tabs.map(t => t.text())).toEqual(["Aufnahmen", "Dokumente", "Kalender"]);
		expect(tabs.map(t => t.attributes("href"))).toEqual(["/", "/documents", "/calendar"]);
		expect(tabs[1]!.attributes("data-testid")).toBe("nav-documents");
		expect(tabs[2]!.attributes("data-testid")).toBe("nav-calendar");
	});

	it("switches sections with replace, so tab hops never pile up history entries", async () => {
		const w = await mountSuspended(AppHeader);
		const links = w.findAllComponents({ name: "NuxtLink" });
		// wordmark + the section tabs
		expect(links.length).toBeGreaterThanOrEqual(3);
		expect(links.every(l => l.props("replace") === true)).toBe(true);
	});

	it("marks the tab of the current section as active", async () => {
		useRuntimeConfig().public.calendarEnabled = true;
		viewport.wide = true;
		const cases: [string, string[]][] = [
			["/", ["nav-recordings"]],
			["/recording/rec-a", ["nav-recordings"]],
			["/documents", ["nav-documents"]],
			["/documents/doc-1", ["nav-documents"]],
			["/calendar", ["nav-calendar"]],
			["/live", []],
		];
		for (const [path, expected] of cases) {
			route.path = path;
			const w = await mountSuspended(AppHeader);
			const active = w.findAll("[aria-current=\"page\"]").map(a => a.attributes("data-testid"));
			expect(active, path).toEqual(expected);
		}
	});

	it("is hidden on phones on pages with their own back bar", async () => {
		const hidden = ["/recording/rec-a", "/documents/doc-1", "/live", "/debug", "/share-target"];
		for (const path of hidden) {
			route.path = path;
			const w = await mountSuspended(AppHeader);
			expect(w.find("[data-testid=\"app-header\"]").exists(), path).toBe(false);
		}
		for (const path of ["/", "/documents", "/calendar"]) {
			route.path = path;
			const w = await mountSuspended(AppHeader);
			expect(w.find("[data-testid=\"app-header\"]").exists(), path).toBe(true);
		}
	});

	it("stays visible on every page on wide screens", async () => {
		viewport.wide = true;
		for (const path of ["/recording/rec-a", "/live"]) {
			route.path = path;
			const w = await mountSuspended(AppHeader);
			expect(w.find("[data-testid=\"app-header\"]").exists(), path).toBe(true);
		}
	});

	it("opens the settings drawer from the cog", async () => {
		const drawer = useSettingsDrawer();
		drawer.hide();
		const w = await mountSuspended(AppHeader);
		const cog = w.find("[data-testid=\"nav-settings\"]");
		expect(cog.attributes("aria-label")).toBe("Einstellungen");
		await cog.trigger("click");
		expect(drawer.open.value).toBe(true);
		drawer.hide();
	});
});
