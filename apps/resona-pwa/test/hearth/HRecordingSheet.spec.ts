import { describe, it, expect } from "vitest";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import HRecordingSheet from "~/components/hearth/HRecordingSheet.vue";

describe("HRecordingSheet", () => {
	it("renders the elapsed time formatted as MM:SS", async () => {
		const w = await mountSuspended(HRecordingSheet, {
			props: { elapsed: 65, levels: [] },
		});
		expect(w.find("[data-testid=\"timer\"]").text()).toBe("01:05");
	});

	it("pads short durations to MM:SS", async () => {
		const w = await mountSuspended(HRecordingSheet, {
			props: { elapsed: 5, levels: [] },
		});
		expect(w.find("[data-testid=\"timer\"]").text()).toBe("00:05");
	});

	it("renders one waveform bar per level value", async () => {
		const w = await mountSuspended(HRecordingSheet, {
			props: { elapsed: 0, levels: [0.1, 0.4, 0.9, 0.2, 0.6] },
		});
		expect(w.findAll("[data-testid=\"wave-bar\"]")).toHaveLength(5);
	});

	it("emits stop when the stop button is clicked", async () => {
		const w = await mountSuspended(HRecordingSheet, {
			props: { elapsed: 0, levels: [] },
		});
		await w.find("[data-testid=\"stop\"]").trigger("click");
		expect(w.emitted("stop")).toBeTruthy();
	});

	it("emits cancel when the cancel button is clicked", async () => {
		const w = await mountSuspended(HRecordingSheet, {
			props: { elapsed: 0, levels: [] },
		});
		await w.find("[data-testid=\"cancel\"]").trigger("click");
		expect(w.emitted("cancel")).toBeTruthy();
	});

	it("emits pause when the pause button is clicked while running", async () => {
		const w = await mountSuspended(HRecordingSheet, {
			props: { elapsed: 0, levels: [], paused: false },
		});
		await w.find("[data-testid=\"pause\"]").trigger("click");
		expect(w.emitted("pause")).toBeTruthy();
		expect(w.emitted("resume")).toBeFalsy();
	});

	it("emits resume when the pause button is clicked while paused", async () => {
		const w = await mountSuspended(HRecordingSheet, {
			props: { elapsed: 0, levels: [], paused: true },
		});
		await w.find("[data-testid=\"pause\"]").trigger("click");
		expect(w.emitted("resume")).toBeTruthy();
		expect(w.emitted("pause")).toBeFalsy();
	});
});
