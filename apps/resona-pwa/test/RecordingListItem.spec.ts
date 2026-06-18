import { describe, it, expect } from "vitest";
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
	it("renders the title and status label", async () => {
		const w = await mountSuspended(RecordingListItem, {
			props: { recording: sample },
		});
		expect(w.text()).toContain("Erstgespräch");
		expect(w.text()).toContain("Fertig");
	});

	it("renders a copy button and emits copy on click without emitting open", async () => {
		const w = await mountSuspended(RecordingListItem, {
			props: { recording: sample },
		});
		const copyBtn = w.find("[data-testid=\"copy-action\"]");
		expect(copyBtn.exists()).toBe(true);
		await copyBtn.trigger("click");
		expect(w.emitted("copy")).toBeTruthy();
		expect(w.emitted("open")).toBeFalsy();
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
