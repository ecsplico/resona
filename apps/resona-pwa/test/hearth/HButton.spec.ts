import { describe, it, expect } from "vitest";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import HButton from "~/components/hearth/HButton.vue";

describe("HButton", () => {
	it("renders the label slot", async () => {
		const w = await mountSuspended(HButton, { slots: { default: () => "Speichern" } });
		expect(w.text()).toContain("Speichern");
	});
	it("primary variant uses the accent background class", async () => {
		const w = await mountSuspended(HButton, { props: { variant: "primary" } });
		expect(w.attributes("class")).toContain("h-btn--primary");
	});
	it("secondary variant uses the outline class", async () => {
		const w = await mountSuspended(HButton, { props: { variant: "secondary" } });
		expect(w.attributes("class")).toContain("h-btn--secondary");
	});
	it("defaults to primary when no variant", async () => {
		const w = await mountSuspended(HButton);
		expect(w.attributes("class")).toContain("h-btn--primary");
	});
	it("sets the disabled attribute", async () => {
		const w = await mountSuspended(HButton, { props: { disabled: true } });
		expect(w.attributes("disabled")).toBeDefined();
	});
});
