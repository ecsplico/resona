import { describe, it, expect } from "vitest";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import HChip from "~/components/hearth/HChip.vue";

describe("HChip", () => {
	it("defaults to the neutral tone", async () => {
		const w = await mountSuspended(HChip);
		expect(w.attributes("class")).toContain("h-chip--neutral");
	});
	it("success tone adds the success class", async () => {
		const w = await mountSuspended(HChip, { props: { tone: "success" } });
		expect(w.attributes("class")).toContain("h-chip--success");
	});
	it("warning tone adds the warning class", async () => {
		const w = await mountSuspended(HChip, { props: { tone: "warning" } });
		expect(w.attributes("class")).toContain("h-chip--warning");
	});
	it("neutral tone adds the neutral class", async () => {
		const w = await mountSuspended(HChip, { props: { tone: "neutral" } });
		expect(w.attributes("class")).toContain("h-chip--neutral");
	});
	it("danger tone adds the danger class", async () => {
		const w = await mountSuspended(HChip, { props: { tone: "danger" } });
		expect(w.attributes("class")).toContain("h-chip--danger");
	});
	it("renders the dot when dot is true", async () => {
		const w = await mountSuspended(HChip, { props: { dot: true } });
		expect(w.find(".h-chip__dot").exists()).toBe(true);
	});
	it("does not render the dot when dot is false", async () => {
		const w = await mountSuspended(HChip, { props: { dot: false } });
		expect(w.find(".h-chip__dot").exists()).toBe(false);
	});
	it("does not render the dot by default", async () => {
		const w = await mountSuspended(HChip);
		expect(w.find(".h-chip__dot").exists()).toBe(false);
	});
	it("renders the default slot label", async () => {
		const w = await mountSuspended(HChip, { slots: { default: () => "Fertig" } });
		expect(w.text()).toContain("Fertig");
	});
});
