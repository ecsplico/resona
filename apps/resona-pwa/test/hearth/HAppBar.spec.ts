import { describe, it, expect } from "vitest";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import HAppBar from "~/components/hearth/HAppBar.vue";

describe("HAppBar", () => {
	it("defaults to the dark tone", async () => {
		const w = await mountSuspended(HAppBar);
		expect(w.find(".h-appbar--dark").exists()).toBe(true);
	});
	it("applies the light tone class", async () => {
		const w = await mountSuspended(HAppBar, { props: { tone: "light" } });
		expect(w.find(".h-appbar--light").exists()).toBe(true);
	});
	it("renders the title prop", async () => {
		const w = await mountSuspended(HAppBar, { props: { title: "Resona" } });
		expect(w.text()).toContain("Resona");
	});
	it("renders the leading and trailing slots", async () => {
		const w = await mountSuspended(HAppBar, {
			slots: { leading: () => "L", trailing: () => "T" },
		});
		expect(w.text()).toContain("L");
		expect(w.text()).toContain("T");
	});
});
