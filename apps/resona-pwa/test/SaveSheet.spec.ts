import { describe, it, expect, vi, beforeEach } from "vitest";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import SaveSheet from "~/components/SaveSheet.vue";

const list = vi.hoisted(() => vi.fn(async () => ["default", "medical"]));

mockNuxtImport("useProfiles", () => () => ({ list }));
// runtimeConfig defaults (defaultLanguage "de", defaultProfile "default") come
// from the real nuxt.config — no need to mock useRuntimeConfig here.

describe("SaveSheet", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		list.mockResolvedValue(["default", "medical"]);
	});

	it("renders title, language, profile pickers and a transcribe toggle", async () => {
		const w = await mountSuspended(SaveSheet);
		expect(w.find("[data-testid=\"title\"]").exists()).toBe(true);
		expect(w.find("[data-testid=\"language-select\"]").exists()).toBe(true);
		expect(w.find("[data-testid=\"profile-select\"]").exists()).toBe(true);
		expect(w.find("[data-testid=\"transcribe-toggle\"]").exists()).toBe(true);
	});

	it("disables Save until the title is non-empty", async () => {
		const w = await mountSuspended(SaveSheet);
		const save = w.find("[data-testid=\"save\"]");
		expect((save.element as HTMLButtonElement).disabled).toBe(true);
		await w.find("[data-testid=\"title\"] input").setValue("Meine Aufnahme");
		expect((save.element as HTMLButtonElement).disabled).toBe(false);
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
