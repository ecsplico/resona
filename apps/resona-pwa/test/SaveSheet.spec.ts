import { describe, it, expect, vi, beforeEach } from "vitest";
import { computed, ref } from "vue";
import { flushPromises } from "@vue/test-utils";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import SaveSheet from "~/components/SaveSheet.vue";

// `wide` normally comes from the layout's media query; drive it directly here.
const viewport = vi.hoisted(() => ({ wide: false }));
mockNuxtImport("useShell", () => () => ({ wide: ref(viewport.wide), paneInLayout: computed(() => false) }));

const list = vi.hoisted(() => vi.fn(async () => ["default", "medical"]));

mockNuxtImport("useProfiles", () => () => ({ list }));
// runtimeConfig defaults (defaultLanguage "de", defaultProfile "default") come
// from the real nuxt.config — no need to mock useRuntimeConfig here.

describe("SaveSheet", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		list.mockResolvedValue(["default", "medical"]);
		viewport.wide = false;
	});

	it("is a modal dialog that starts in the title field on wide screens", async () => {
		viewport.wide = true;
		const w = await mountSuspended(SaveSheet, { attachTo: document.body });
		await flushPromises();
		const dialog = w.find("[role=\"dialog\"]");
		expect(dialog.attributes("aria-modal")).toBe("true");
		expect(dialog.attributes("aria-labelledby")).toBeTruthy();
		expect(document.activeElement).toBe(w.find("[data-testid=\"title\"] input").element);
		w.unmount();
	});

	it("does not focus the title on phones (no keyboard popping up)", async () => {
		const w = await mountSuspended(SaveSheet, { attachTo: document.body });
		await flushPromises();
		expect(document.activeElement).not.toBe(w.find("[data-testid=\"title\"] input").element);
		w.unmount();
	});

	it("renders title, language, profile pickers and a transcribe toggle", async () => {
		const w = await mountSuspended(SaveSheet);
		expect(w.find("[data-testid=\"title\"]").exists()).toBe(true);
		expect(w.find("[data-testid=\"language-select\"]").exists()).toBe(true);
		expect(w.find("[data-testid=\"profile-select\"]").exists()).toBe(true);
		expect(w.find("[data-testid=\"transcribe-toggle\"]").exists()).toBe(true);
	});

	it("saves without a title: placeholder title, auto when transcribing", async () => {
		const w = await mountSuspended(SaveSheet);
		const save = w.find("[data-testid=\"save\"]");
		expect((save.element as HTMLButtonElement).disabled).toBe(false);
		await save.trigger("click");
		const payload = w.emitted("save")![0]![0] as { title: string; titleAuto: boolean };
		expect(payload.title).toMatch(/^Aufnahme \d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$/);
		expect(payload.titleAuto).toBe(true);
	});

	it("keeps the placeholder title non-auto when not transcribing", async () => {
		const w = await mountSuspended(SaveSheet);
		await w.find("[data-testid=\"transcribe-toggle\"]").trigger("click");
		await w.find("[data-testid=\"save\"]").trigger("click");
		const payload = w.emitted("save")![0]![0] as { title: string; titleAuto: boolean };
		expect(payload.title).toMatch(/^Aufnahme /);
		expect(payload.titleAuto).toBe(false);
	});

	it("emits save with the picked profile/language (not hardcoded)", async () => {
		const w = await mountSuspended(SaveSheet);
		await w.find("[data-testid=\"title\"] input").setValue("Visite");
		await w.find("[data-testid=\"language-select\"]").setValue("en");
		await w.find("[data-testid=\"profile-select\"]").setValue("medical");
		await w.find("[data-testid=\"save\"]").trigger("click");

		const ev = w.emitted("save");
		expect(ev).toBeTruthy();
		expect(ev![0]![0]).toEqual({
			title: "Visite",
			titleAuto: false,
			language: "en",
			profile: "medical",
			transcribe: true,
		});
	});

	it("hides the language/profile pickers when transcribe is off", async () => {
		const w = await mountSuspended(SaveSheet);
		expect(w.find("[data-testid=\"profile-select\"]").exists()).toBe(true);
		await w.find("[data-testid=\"transcribe-toggle\"]").trigger("click");
		expect(w.find("[data-testid=\"profile-select\"]").exists()).toBe(false);
		expect(w.find("[data-testid=\"language-select\"]").exists()).toBe(false);
	});

	it("emits discard when Discard is clicked", async () => {
		const w = await mountSuspended(SaveSheet);
		await w.find("[data-testid=\"discard\"]").trigger("click");
		expect(w.emitted("discard")).toBeTruthy();
	});
});
