import { describe, it, expect, vi, beforeEach } from "vitest";
import { defineComponent, h, onMounted, reactive, ref } from "vue";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import type * as VueUse from "@vueuse/core";
import DefaultLayout from "~/layouts/default.vue";

const route = vi.hoisted(() => ({ path: "/" }));
mockNuxtImport("useRoute", () => () => reactive(route));

// The layout decides the width with a media query; drive it directly here.
const viewport = vi.hoisted(() => ({ wide: false }));
vi.mock("@vueuse/core", async (importOriginal) => {
	const actual = await importOriginal<typeof VueUse>();
	return { ...actual, useMediaQuery: () => ref(viewport.wide) };
});

const paneMounts = vi.hoisted(() => ({ count: 0 }));
const RecordingsPaneStub = defineComponent({
	name: "RecordingsPane",
	setup() {
		onMounted(() => {
			paneMounts.count++;
		});
		return () => h("div", { "data-testid": "pane-stub" });
	},
});

function mountLayout() {
	return mountSuspended(DefaultLayout, {
		slots: { default: () => h("p", { "data-testid": "page" }, "Seite") },
		global: { stubs: { AppHeader: true, SettingsDrawer: true, RecordingsPane: RecordingsPaneStub } },
	});
}

describe("default layout", () => {
	beforeEach(() => {
		route.path = "/";
		viewport.wide = false;
		paneMounts.count = 0;
	});

	it("keeps phones single-column: the page alone under the header", async () => {
		const w = await mountLayout();
		expect(w.find("[data-testid=\"workspace\"]").exists()).toBe(false);
		expect(w.find("[data-testid=\"pane-stub\"]").exists()).toBe(false);
		expect(w.find("main [data-testid=\"page\"]").exists()).toBe(true);
	});

	it("puts the recordings pane in an aside next to the page on wide screens", async () => {
		viewport.wide = true;
		const w = await mountLayout();
		const workspace = w.find("[data-testid=\"workspace\"]");
		expect(workspace.find("aside [data-testid=\"pane-stub\"]").exists()).toBe(true);
		expect(workspace.find("main [data-testid=\"page\"]").exists()).toBe(true);
	});

	it("keeps the same pane while moving between the list and a recording", async () => {
		viewport.wide = true;
		const w = await mountLayout();
		reactive(route).path = "/recording/rec-a";
		await w.vm.$nextTick();
		expect(w.find("aside [data-testid=\"pane-stub\"]").exists()).toBe(true);
		reactive(route).path = "/recording/rec-b";
		await w.vm.$nextTick();
		expect(paneMounts.count).toBe(1);
	});

	it("gives other sections the full width without the pane", async () => {
		viewport.wide = true;
		route.path = "/documents";
		const w = await mountLayout();
		expect(w.find("[data-testid=\"workspace\"]").exists()).toBe(false);
		expect(w.find("main [data-testid=\"page\"]").exists()).toBe(true);
	});
});
