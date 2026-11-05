import { describe, it, expect, vi, beforeEach } from "vitest";
import { defineComponent, h, onMounted, onUnmounted, reactive, ref } from "vue";
import type { Ref } from "vue";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import type * as VueUse from "@vueuse/core";
import DefaultLayout from "~/layouts/default.vue";
import { useSettingsDrawer } from "~/composables/useShell";

const route = vi.hoisted(() => ({ path: "/" }));
mockNuxtImport("useRoute", () => () => reactive(route));

// The layout decides the width with a media query; drive it directly here.
const viewport = vi.hoisted(() => ({ wide: false, query: null as Ref<boolean> | null }));
vi.mock("@vueuse/core", async (importOriginal) => {
	const actual = await importOriginal<typeof VueUse>();
	return {
		...actual,
		useMediaQuery: () => {
			viewport.query = ref(viewport.wide);
			return viewport.query;
		},
	};
});

const mounts = vi.hoisted(() => ({ pane: 0, page: 0, pageUnmounts: 0 }));
const RecordingsPaneStub = defineComponent({
	name: "RecordingsPane",
	setup() {
		onMounted(() => {
			mounts.pane++;
		});
		return () => h("div", { "data-testid": "pane-stub" });
	},
});
const PageStub = defineComponent({
	name: "PageStub",
	setup() {
		onMounted(() => {
			mounts.page++;
		});
		onUnmounted(() => {
			mounts.pageUnmounts++;
		});
		return () => h("p", { "data-testid": "page" }, "Seite");
	},
});

function mountLayout() {
	return mountSuspended(DefaultLayout, {
		slots: { default: () => h(PageStub) },
		global: { stubs: { AppHeader: true, SettingsDrawer: true, RecordingsPane: RecordingsPaneStub } },
	});
}

describe("default layout", () => {
	beforeEach(() => {
		route.path = "/";
		viewport.wide = false;
		mounts.pane = 0;
		mounts.page = 0;
		mounts.pageUnmounts = 0;
		useSettingsDrawer().hide();
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
		expect(mounts.pane).toBe(1);
	});

	it("keeps the page mounted when the viewport crosses the two-pane breakpoint", async () => {
		viewport.wide = true;
		reactive(route).path = "/recording/rec-a";
		const w = await mountLayout();
		expect(w.find("aside [data-testid=\"pane-stub\"]").exists()).toBe(true);

		viewport.query!.value = false;
		await w.vm.$nextTick();
		expect(w.find("[data-testid=\"pane-stub\"]").exists()).toBe(false);
		expect(w.find("main [data-testid=\"page\"]").exists()).toBe(true);

		viewport.query!.value = true;
		await w.vm.$nextTick();
		expect(w.find("aside [data-testid=\"pane-stub\"]").exists()).toBe(true);
		expect(mounts.page).toBe(1);
		expect(mounts.pageUnmounts).toBe(0);
	});

	it("gives other sections the full width without the pane", async () => {
		viewport.wide = true;
		route.path = "/documents";
		const w = await mountLayout();
		expect(w.find("[data-testid=\"workspace\"]").exists()).toBe(false);
		expect(w.find("main [data-testid=\"page\"]").exists()).toBe(true);
	});

	it("closes the settings drawer when the route changes", async () => {
		const w = await mountLayout();
		const drawer = useSettingsDrawer();
		drawer.show();
		await w.vm.$nextTick();
		reactive(route).path = "/documents";
		await w.vm.$nextTick();
		expect(drawer.open.value).toBe(false);
	});
});
