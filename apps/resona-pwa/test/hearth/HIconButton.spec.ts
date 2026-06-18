import { describe, it, expect } from "vitest";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import HIconButton from "~/components/hearth/HIconButton.vue";

describe("HIconButton", () => {
	it("renders the icon slot", async () => {
		const w = await mountSuspended(HIconButton, { slots: { default: () => "icon" } });
		expect(w.text()).toContain("icon");
	});
	it("glass tone adds the glass class", async () => {
		const w = await mountSuspended(HIconButton, { props: { tone: "glass" } });
		expect(w.attributes("class")).toContain("h-iconbtn--glass");
	});
	it("defaults to the cream tone", async () => {
		const w = await mountSuspended(HIconButton);
		expect(w.attributes("class")).toContain("h-iconbtn--cream");
	});
	it("applies a custom size to the inline style", async () => {
		const w = await mountSuspended(HIconButton, { props: { size: 42 } });
		const style = w.attributes("style") ?? "";
		expect(style).toContain("width: 42px");
		expect(style).toContain("height: 42px");
	});
});
