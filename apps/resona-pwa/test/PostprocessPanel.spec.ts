import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref } from "vue";
import { flushPromises } from "@vue/test-utils";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import PostprocessPanel from "~/components/PostprocessPanel.vue";
import type { Transcript } from "~/types/directus";

const hubProfiles = vi.hoisted(() => ({ names: ["default", "transcript-markdown-de", "transcript-polish-de"] }));
mockNuxtImport("useProfiles", () => () => {
	const profiles = ref<string[]>([]);
	return {
		profiles,
		list: vi.fn(async () => {
			profiles.value = hubProfiles.names;
			return hubProfiles.names;
		}),
	};
});

const store = vi.hoisted(() => ({ updateTranscript: vi.fn(async () => {}) }));
mockNuxtImport("useDataStore", () => () => store);

const RAW = "Kapitel Befund Absatz Patient wach";

function transcript(extra: Partial<Transcript> = {}): Transcript {
	return { id: "t-1", recording: "rec-1", text: RAW, ...extra };
}

async function mount(t: Transcript, blocked: string | null = null) {
	const w = await mountSuspended(PostprocessPanel, { props: { transcript: t, blocked } });
	await flushPromises();
	return w;
}

describe("PostprocessPanel", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		store.updateTranscript.mockResolvedValue(undefined);
	});

	it("lists the hub profiles and requests a run of the chosen one", async () => {
		const w = await mount(transcript());
		const options = w.findAll("[data-testid=\"postprocess-profile\"] option").map(o => o.text());
		expect(options).toEqual(hubProfiles.names);
		await w.find("[data-testid=\"postprocess-profile\"]").setValue("transcript-markdown-de");
		await w.find("[data-testid=\"postprocess-run\"]").trigger("click");
		await flushPromises();
		expect(store.updateTranscript).toHaveBeenCalledWith("t-1", {
			postprocess_status: "pending",
			postprocess_profile: "transcript-markdown-de",
			postprocess_error: null,
			postprocess_queue_reason: null,
			postprocess_job_id: null,
			postprocess_job_key: null,
		});
		expect(w.emitted("changed")).toHaveLength(1);
	});

	it("preselects the profile last run on the transcript", async () => {
		const w = await mount(transcript({ postprocess_profile: "transcript-polish-de", postprocess_status: "error" }));
		expect((w.find("[data-testid=\"postprocess-profile\"]").element as HTMLSelectElement).value).toBe("transcript-polish-de");
	});

	it("is unavailable with a reason while blocked (e.g. unsaved edits)", async () => {
		const w = await mount(transcript(), "Erst speichern oder verwerfen, dann nachbearbeiten.");
		expect(w.find("[data-testid=\"postprocess-run\"]").attributes("disabled")).toBeDefined();
		expect(w.find("[data-testid=\"postprocess-blocked\"]").text()).toContain("Erst speichern");
	});

	it("shows a running job and why it waits when no capable worker is online", async () => {
		const w = await mount(transcript({
			postprocess_status: "processing",
			postprocess_profile: "transcript-polish-de",
			postprocess_queue_reason: "no_capable_worker",
		}));
		expect(w.find("[data-testid=\"postprocess-running\"]").text()).toContain("„transcript-polish-de“");
		expect(w.find("[data-testid=\"postprocess-queue\"]").text()).toContain("Kein passender Worker online");
		expect(w.find("[data-testid=\"postprocess-run\"]").attributes("disabled")).toBeDefined();
		expect(w.find("[data-testid=\"postprocess-restore\"]").exists()).toBe(false);
	});

	it("shows the error of a failed run (e.g. policy_denied)", async () => {
		const w = await mount(transcript({
			postprocess_status: "error",
			postprocess_error: "Von der Richtlinie der Organisation abgelehnt: endpoint not assigned",
		}));
		expect(w.find("[data-testid=\"postprocess-error\"]").text()).toBe("Von der Richtlinie der Organisation abgelehnt: endpoint not assigned");
		expect(w.find("[data-testid=\"postprocess-run\"]").attributes("disabled")).toBeUndefined();
	});

	it("keeps the raw transcript visible after a run and restores it on request", async () => {
		const t = transcript({
			text: "## Befund\n\nPatient wach",
			raw_text: RAW,
			applied_profile: "transcript-markdown-de",
			postprocess_status: "done",
		});
		const w = await mount(t);
		const raw = w.find("[data-testid=\"postprocess-raw\"]");
		expect(raw.find("summary").text()).toContain("„transcript-markdown-de“");
		expect(raw.find("pre").text()).toBe(RAW);
		await w.find("[data-testid=\"postprocess-restore\"]").trigger("click");
		await flushPromises();
		expect(store.updateTranscript).toHaveBeenCalledWith("t-1", {
			text: RAW, raw_text: null, applied_profile: null, postprocess_status: null, postprocess_error: null,
		});
		expect(w.emitted("changed")).toHaveLength(1);
	});

	it("reports a failed request write and stays put", async () => {
		store.updateTranscript.mockRejectedValueOnce(new Error("offline"));
		const w = await mount(transcript());
		await w.find("[data-testid=\"postprocess-run\"]").trigger("click");
		await flushPromises();
		expect(w.find("[data-testid=\"postprocess-action-error\"]").exists()).toBe(true);
		expect(w.emitted("changed")).toBeUndefined();
	});
});
