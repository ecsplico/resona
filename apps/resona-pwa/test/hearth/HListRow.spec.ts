import { describe, it, expect } from "vitest";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import HListRow from "~/components/hearth/HListRow.vue";

const base = {
	title: "Aufnahme vom Montag",
	status: "done" as const,
};

describe("HListRow", () => {
	it("renders the title", async () => {
		const w = await mountSuspended(HListRow, { props: base });
		expect(w.text()).toContain("Aufnahme vom Montag");
	});
	it("renders the meta when provided", async () => {
		const w = await mountSuspended(HListRow, { props: { ...base, meta: "02:13 · de" } });
		expect(w.text()).toContain("02:13 · de");
	});
	it("renders a StatusChip reflecting the status", async () => {
		const w = await mountSuspended(HListRow, { props: { ...base, status: "done" } });
		expect(w.text()).toContain("Fertig");
		expect(w.find(".h-chip").exists()).toBe(true);
	});
	it("adds the selected class via HCard when selected", async () => {
		const w = await mountSuspended(HListRow, { props: { ...base, selected: true } });
		expect(w.find(".h-card--selected").exists()).toBe(true);
	});
	it("is not selected by default", async () => {
		const w = await mountSuspended(HListRow, { props: base });
		expect(w.find(".h-card--selected").exists()).toBe(false);
	});
	it("emits select when clicked", async () => {
		const w = await mountSuspended(HListRow, { props: base });
		await w.find(".h-card").trigger("click");
		expect(w.emitted("select")).toBeTruthy();
	});
	it("renders the preview when provided", async () => {
		const w = await mountSuspended(HListRow, { props: { ...base, preview: "Guten Tag…" } });
		expect(w.text()).toContain("Guten Tag…");
	});
	it("does not render a preview when absent", async () => {
		const w = await mountSuspended(HListRow, { props: base });
		expect(w.find(".h-listrow__preview").exists()).toBe(false);
	});
	it("renders the actions slot", async () => {
		const w = await mountSuspended(HListRow, {
			props: base,
			slots: { actions: () => "ACT" },
		});
		expect(w.text()).toContain("ACT");
	});
});
