import { describe, it, expect } from "vitest";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import HCard from "~/components/hearth/HCard.vue";

describe("HCard", () => {
	it("renders the default slot", async () => {
		const w = await mountSuspended(HCard, { slots: { default: () => "Inhalt" } });
		expect(w.text()).toContain("Inhalt");
	});
	it("is not selected by default", async () => {
		const w = await mountSuspended(HCard);
		expect(w.find(".h-card--selected").exists()).toBe(false);
	});
	it("adds the selected class when selected", async () => {
		const w = await mountSuspended(HCard, { props: { selected: true } });
		expect(w.find(".h-card--selected").exists()).toBe(true);
	});
});
