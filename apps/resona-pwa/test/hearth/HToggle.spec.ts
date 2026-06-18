import { describe, it, expect } from "vitest";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import HToggle from "~/components/hearth/HToggle.vue";

describe("HToggle", () => {
	it("renders off by default", async () => {
		const w = await mountSuspended(HToggle);
		expect(w.classes()).not.toContain("h-toggle--on");
		expect(w.attributes("aria-checked")).toBe("false");
	});
	it("renders on when modelValue is true", async () => {
		const w = await mountSuspended(HToggle, { props: { modelValue: true } });
		expect(w.classes()).toContain("h-toggle--on");
		expect(w.attributes("aria-checked")).toBe("true");
	});
	it("has role switch", async () => {
		const w = await mountSuspended(HToggle);
		expect(w.attributes("role")).toBe("switch");
	});
	it("clicking emits update:modelValue with the negated value when off", async () => {
		const w = await mountSuspended(HToggle, { props: { modelValue: false } });
		await w.trigger("click");
		expect(w.emitted("update:modelValue")![0]).toEqual([true]);
	});
	it("clicking emits update:modelValue with the negated value when on", async () => {
		const w = await mountSuspended(HToggle, { props: { modelValue: true } });
		await w.trigger("click");
		expect(w.emitted("update:modelValue")![0]).toEqual([false]);
	});
});
