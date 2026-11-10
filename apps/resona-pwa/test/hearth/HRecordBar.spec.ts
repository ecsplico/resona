import { describe, it, expect } from "vitest";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import HRecordBar from "~/components/hearth/HRecordBar.vue";

describe("HRecordBar", () => {
	it("orders the buttons like the recording sheet: discard, pause, stop", async () => {
		const w = await mountSuspended(HRecordBar, { props: { elapsed: 0, levels: [] } });
		const buttons = w.findAll("button");
		expect(buttons.map(b => b.attributes("data-testid"))).toEqual(["cancel", "pause", "stop"]);
		expect(buttons.map(b => b.attributes("aria-label"))).toEqual(["Verwerfen", "Pausieren", "Aufnahme beenden"]);
	});

	it("emits cancel, pause and stop from the matching buttons", async () => {
		const w = await mountSuspended(HRecordBar, { props: { elapsed: 0, levels: [] } });
		await w.find("[data-testid=\"cancel\"]").trigger("click");
		await w.find("[data-testid=\"pause\"]").trigger("click");
		await w.find("[data-testid=\"stop\"]").trigger("click");
		expect(w.emitted("cancel")).toHaveLength(1);
		expect(w.emitted("pause")).toHaveLength(1);
		expect(w.emitted("stop")).toHaveLength(1);
		expect(w.emitted("resume")).toBeUndefined();
	});

	it("offers Fortsetzen and emits resume while paused", async () => {
		const w = await mountSuspended(HRecordBar, { props: { elapsed: 0, levels: [], paused: true } });
		const pause = w.find("[data-testid=\"pause\"]");
		expect(pause.attributes("aria-label")).toBe("Fortsetzen");
		await pause.trigger("click");
		expect(w.emitted("resume")).toHaveLength(1);
		expect(w.emitted("pause")).toBeUndefined();
	});

	it("formats the elapsed time as MM:SS", async () => {
		const w = await mountSuspended(HRecordBar, { props: { elapsed: 65.7, levels: [] } });
		expect(w.find("[data-testid=\"timer\"]").text()).toBe("01:05");
	});

	it("labels the bar Nachdiktat by default, or with the given label", async () => {
		const plain = await mountSuspended(HRecordBar, { props: { elapsed: 0, levels: [] } });
		expect(plain.find("[data-testid=\"record-bar-label\"]").text()).toBe("Nachdiktat");
		expect(plain.find("[data-testid=\"record-bar\"]").attributes("aria-label")).toBe("Nachdiktat");
		const labelled = await mountSuspended(HRecordBar, { props: { elapsed: 0, levels: [], label: "Ergänzen" } });
		expect(labelled.find("[data-testid=\"record-bar-label\"]").text()).toBe("Ergänzen");
	});

	it("shows how many parts are still being transcribed", async () => {
		const w = await mountSuspended(HRecordBar, { props: { elapsed: 0, levels: [] } });
		const status = () => w.find("[data-testid=\"record-bar-pending\"]");
		expect(status().text()).toBe("");
		expect(status().attributes("role")).toBe("status");
		await w.setProps({ pending: 1 });
		expect(status().text()).toBe("1 Teil wird transkribiert");
		await w.setProps({ pending: 3 });
		expect(status().text()).toBe("3 Teile werden transkribiert");
		await w.setProps({ pending: 0 });
		expect(status().text()).toBe("");
	});

	it("keeps only the newest levels in the compact meter", async () => {
		const levels = Array.from({ length: 100 }, (_, i) => (i % 10) / 10);
		const w = await mountSuspended(HRecordBar, { props: { elapsed: 0, levels } });
		const bars = w.findAll("[data-testid=\"wave-bar\"]");
		expect(bars.length).toBeGreaterThan(0);
		expect(bars.length).toBeLessThanOrEqual(24);
	});
});
