import { describe, it, expect } from "vitest";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import StatusChip from "~/components/StatusChip.vue";

describe("StatusChip", () => {
	it("maps done to the Fertig success chip", async () => {
		const w = await mountSuspended(StatusChip, { props: { status: "done" } });
		expect(w.text()).toContain("Fertig");
		expect(w.find(".h-chip--success").exists()).toBe(true);
	});
	it("maps transcribing to the warning chip with a dot", async () => {
		const w = await mountSuspended(StatusChip, { props: { status: "transcribing" } });
		expect(w.text()).toContain("Transkribiert…");
		expect(w.find(".h-chip--warning").exists()).toBe(true);
		expect(w.find(".h-chip__dot").exists()).toBe(true);
	});
	it("maps pending to the Wartet neutral chip", async () => {
		const w = await mountSuspended(StatusChip, { props: { status: "pending" } });
		expect(w.text()).toContain("Wartet");
		expect(w.find(".h-chip--neutral").exists()).toBe(true);
	});
	it("maps error to the Fehler danger chip", async () => {
		const w = await mountSuspended(StatusChip, { props: { status: "error" } });
		expect(w.text()).toContain("Fehler");
		expect(w.find(".h-chip--danger").exists()).toBe(true);
	});
});
