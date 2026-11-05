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
	it("shows no status chip for a done row", async () => {
		const w = await mountSuspended(HListRow, { props: { ...base, status: "done" } });
		expect(w.find(".h-chip").exists()).toBe(false);
		expect(w.text()).not.toContain("Fertig");
	});
	it.each([
		["pending", "Wartet"],
		["transcribing", "Transkribiert…"],
		["error", "Fehler"],
	] as const)("shows a status chip for a %s row", async (status, label) => {
		const w = await mountSuspended(HListRow, { props: { ...base, status } });
		expect(w.find(".h-chip").exists()).toBe(true);
		expect(w.text()).toContain(label);
	});
	it("marks the row selected with a class and aria-current", async () => {
		const w = await mountSuspended(HListRow, { props: { ...base, selected: true } });
		expect(w.find(".h-listrow--selected").exists()).toBe(true);
		expect(w.find(".h-listrow__hit").attributes("aria-current")).toBe("page");
	});
	it("is not selected by default", async () => {
		const w = await mountSuspended(HListRow, { props: base });
		expect(w.find(".h-listrow--selected").exists()).toBe(false);
		expect(w.find(".h-listrow__hit").attributes("aria-current")).toBeUndefined();
	});
	it("emits select once when the row button is clicked", async () => {
		const w = await mountSuspended(HListRow, { props: base });
		await w.find(".h-listrow__hit").trigger("click");
		expect(w.emitted("select")).toHaveLength(1);
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
