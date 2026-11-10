import { describe, it, expect, vi, beforeEach } from "vitest";
import { computed, ref } from "vue";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import HRecordingSheet from "~/components/hearth/HRecordingSheet.vue";

// `wide` normally comes from the layout's media query; drive it directly here.
const viewport = vi.hoisted(() => ({ wide: false }));
mockNuxtImport("useShell", () => () => ({ wide: ref(viewport.wide), paneInLayout: computed(() => false) }));

describe("HRecordingSheet", () => {
	beforeEach(() => {
		viewport.wide = false;
	});

	it("is a modal dialog that puts keyboard focus on the central pause button on wide screens", async () => {
		viewport.wide = true;
		const w = await mountSuspended(HRecordingSheet, {
			props: { elapsed: 0, levels: [] },
			attachTo: document.body,
		});
		const dialog = w.find("[role=\"dialog\"]");
		expect(dialog.attributes("aria-modal")).toBe("true");
		expect(dialog.attributes("aria-labelledby")).toBeTruthy();
		expect(document.activeElement).toBe(w.find("[data-testid=\"pause\"]").element);
		w.unmount();
	});

	it("leaves focus alone on phones", async () => {
		const w = await mountSuspended(HRecordingSheet, {
			props: { elapsed: 0, levels: [] },
			attachTo: document.body,
		});
		expect(document.activeElement).not.toBe(w.find("[data-testid=\"pause\"]").element);
		w.unmount();
	});

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

	it("shows the default heading, or the label when given", async () => {
		const plain = await mountSuspended(HRecordingSheet, {
			props: { elapsed: 0, levels: [] },
		});
		expect(plain.find("#h-rec-title").text()).toBe("AUFNAHME LÄUFT");
		const labelled = await mountSuspended(HRecordingSheet, {
			props: { elapsed: 0, levels: [], label: "Weiter aufnehmen" },
		});
		expect(labelled.find("#h-rec-title").text()).toBe("Weiter aufnehmen");
	});

	it("reads PAUSIERT and stills the pulse dot while paused", async () => {
		const running = await mountSuspended(HRecordingSheet, {
			props: { elapsed: 0, levels: [], label: "Weiter aufnehmen" },
		});
		expect(running.find("[data-testid=\"pulse\"]").classes()).not.toContain("is-paused");
		const paused = await mountSuspended(HRecordingSheet, {
			props: { elapsed: 0, levels: [], label: "Weiter aufnehmen", paused: true },
		});
		expect(paused.find("#h-rec-title").text()).toBe("PAUSIERT");
		expect(paused.find("[data-testid=\"pulse\"]").classes()).toContain("is-paused");
	});

	it("renders one waveform bar per level value", async () => {
		const w = await mountSuspended(HRecordingSheet, {
			props: { elapsed: 0, levels: [0.1, 0.4, 0.9, 0.2, 0.6] },
		});
		expect(w.findAll("[data-testid=\"wave-bar\"]")).toHaveLength(5);
	});

	it("shows only the newest bars that fit and scales quiet levels up", async () => {
		const levels = Array.from({ length: 100 }, (_, i) => (i === 99 ? 0.1 : 0.2));
		const w = await mountSuspended(HRecordingSheet, { props: { elapsed: 0, levels } });
		const bars = w.findAll("[data-testid=\"wave-bar\"]");
		expect(bars).toHaveLength(44);
		expect(bars.at(-1)!.attributes("style")).toContain("height: 30%");
		expect(bars[0]!.attributes("style")).toContain("height: 60%");
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

	it("lays out cancel left, the big pause/resume center and stop right", async () => {
		const w = await mountSuspended(HRecordingSheet, {
			props: { elapsed: 0, levels: [], paused: false },
		});
		const buttons = w.findAll(".h-rec__controls button");
		expect(buttons.map(b => b.attributes("data-testid"))).toEqual(["cancel", "pause", "stop"]);
		expect(buttons.map(b => b.attributes("aria-label"))).toEqual(["Verwerfen", "Pausieren", "Aufnahme beenden"]);

		await buttons[0]!.trigger("click");
		await buttons[1]!.trigger("click");
		await buttons[2]!.trigger("click");
		expect(w.emitted("cancel")).toHaveLength(1);
		expect(w.emitted("pause")).toHaveLength(1);
		expect(w.emitted("stop")).toHaveLength(1);
		expect(w.emitted("resume")).toBeFalsy();

		await w.setProps({ paused: true });
		expect(buttons[1]!.attributes("aria-label")).toBe("Fortsetzen");
		await buttons[1]!.trigger("click");
		expect(w.emitted("resume")).toHaveLength(1);
		expect(w.emitted("pause")).toHaveLength(1);
		expect(w.emitted("stop")).toHaveLength(1);
	});
});
