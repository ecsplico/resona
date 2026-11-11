import { describe, it, expect } from "vitest";
import { flushPromises } from "@vue/test-utils";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import RecordingListItem from "~/components/RecordingListItem.vue";
import type { Recording } from "~/types/directus";

const sample: Recording = {
	id: "rec-1",
	title: "Erstgespräch",
	date_created: "2026-06-19T08:30:00.000Z",
	duration_seconds: 42,
	language: "de",
	profile: "default",
	status: "done",
	source: "batch",
};

describe("RecordingListItem", () => {
	it("renders the title and no chip for a transcribed recording", async () => {
		const w = await mountSuspended(RecordingListItem, {
			props: { recording: sample },
		});
		expect(w.text()).toContain("Erstgespräch");
		expect(w.find(".h-chip").exists()).toBe(false);
	});

	it.each([
		["pending", "Wartet"],
		["transcribing", "Transkribiert…"],
		["error", "Fehler"],
	] as const)("shows a status chip for a %s recording", async (status, label) => {
		const w = await mountSuspended(RecordingListItem, {
			props: { recording: { ...sample, status } },
		});
		expect(w.find(".h-chip").exists()).toBe(true);
		expect(w.text()).toContain(label);
	});

	it("passes the selected state to the row", async () => {
		const w = await mountSuspended(RecordingListItem, {
			props: { recording: sample, selected: true },
		});
		expect(w.find(".h-listrow--selected").exists()).toBe(true);
	});

	it("renders a labelled copy button and emits copy on click without emitting open", async () => {
		const w = await mountSuspended(RecordingListItem, {
			props: { recording: sample },
		});
		const copyBtn = w.find("[data-testid=\"copy-action\"]");
		expect(copyBtn.exists()).toBe(true);
		expect(copyBtn.attributes("aria-label")).toBe("Kopieren");
		await copyBtn.trigger("click");
		expect(w.emitted("copy")).toBeTruthy();
		expect(w.emitted("open")).toBeFalsy();
	});

	it("opens the overflow menu next to copy without opening the recording", async () => {
		const w = await mountSuspended(RecordingListItem, {
			props: { recording: sample },
		});
		const trigger = w.find("[aria-haspopup=\"menu\"]");
		expect(trigger.attributes("aria-label")).toBe("Weitere Aktionen");
		await trigger.trigger("click");
		await flushPromises();
		expect(w.findAll("[role=\"menuitem\"]").map(i => i.text())).toEqual(["Teilen", "Als Markdown exportieren", "Weiter diktieren", "Löschen"]);
		expect(w.emitted("open")).toBeFalsy();
		expect(w.emitted("menu-open")).toHaveLength(1);
	});

	it.each(["share", "export", "redictate", "delete"] as const)("emits action %s from the menu without emitting open", async (item) => {
		const w = await mountSuspended(RecordingListItem, {
			props: { recording: sample },
		});
		await w.find("[aria-haspopup=\"menu\"]").trigger("click");
		await flushPromises();
		await w.find(`[data-testid="menu-${item}"]`).trigger("click");
		expect(w.emitted("action")).toEqual([[item]]);
		expect(w.emitted("open")).toBeFalsy();
	});

	it("offers neither Markdown export nor re-dictation before the recording is transcribed", async () => {
		const w = await mountSuspended(RecordingListItem, {
			props: { recording: { ...sample, status: "pending" } },
		});
		await w.find("[aria-haspopup=\"menu\"]").trigger("click");
		await flushPromises();
		expect(w.findAll("[role=\"menuitem\"]").map(i => i.text())).toEqual(["Teilen", "Löschen"]);
	});

	it("offers re-dictation, without export, for a failed transcription", async () => {
		const w = await mountSuspended(RecordingListItem, {
			props: { recording: { ...sample, status: "error" } },
		});
		await w.find("[aria-haspopup=\"menu\"]").trigger("click");
		await flushPromises();
		expect(w.findAll("[role=\"menuitem\"]").map(i => i.text())).toEqual(["Teilen", "Weiter diktieren", "Löschen"]);
	});

	it("emits open when the row is selected", async () => {
		const w = await mountSuspended(RecordingListItem, {
			props: { recording: sample },
		});
		w.findComponent({ name: "HListRow" }).vm.$emit("select");
		await w.vm.$nextTick();
		expect(w.emitted("open")).toBeTruthy();
	});

	it("formats the meta line from date, duration and language", async () => {
		const w = await mountSuspended(RecordingListItem, {
			props: { recording: sample },
		});
		const text = w.text();
		expect(text).toContain("0:42");
		expect(text).toContain("DE");
	});
});
