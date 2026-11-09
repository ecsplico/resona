import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { computed, ref } from "vue";
import { flushPromises } from "@vue/test-utils";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import PostprocessPanel from "~/components/PostprocessPanel.vue";
import type { Transcript } from "~/types/directus";

const hub = vi.hoisted(() => ({
	names: ["default", "transcript-markdown-de", "transcript-polish-de"],
	error: null as string | null,
	token: true,
	list: null as null | ReturnType<typeof vi.fn>,
}));
mockNuxtImport("useProfiles", () => () => {
	const profiles = ref<string[]>([]);
	const error = ref<string | null>(null);
	hub.list = vi.fn(async () => {
		error.value = hub.error;
		profiles.value = hub.error ? ["default"] : hub.names;
		return profiles.value;
	});
	return { profiles, error, list: hub.list };
});
mockNuxtImport("useHubTokenGate", () => () => ({
	blocked: computed(() => !hub.token),
	message: "",
	allowed: () => hub.token,
}));

const store = vi.hoisted(() => ({ updateTranscript: vi.fn(async () => {}) }));
mockNuxtImport("useDataStore", () => () => store);

const RAW = "Kapitel Befund Absatz Patient wach";
const PROCESSED = "## Befund\n\nPatient wach";

function transcript(extra: Partial<Transcript> = {}): Transcript {
	return { id: "t-1", recording: "rec-1", text: RAW, ...extra };
}

function processed(extra: Partial<Transcript> = {}): Transcript {
	return transcript({
		text: PROCESSED,
		raw_text: RAW,
		applied_profile: "transcript-markdown-de",
		processed_text: PROCESSED,
		postprocess_status: "done",
		postprocess_profile: "transcript-markdown-de",
		...extra,
	});
}

async function mount(t: Transcript, blocked: string | null = null) {
	const w = await mountSuspended(PostprocessPanel, { props: { transcript: t, blocked } });
	await flushPromises();
	return w;
}

const run = "[data-testid=\"postprocess-run\"]";
const restore = "[data-testid=\"postprocess-restore\"]";

describe("PostprocessPanel", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		hub.error = null;
		hub.token = true;
		store.updateTranscript.mockResolvedValue(undefined);
	});

	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it("lists the hub profiles and requests a run of the chosen one", async () => {
		const w = await mount(transcript());
		const options = w.findAll("[data-testid=\"postprocess-profile\"] option").map(o => o.text());
		expect(options).toEqual(hub.names);
		await w.find("[data-testid=\"postprocess-profile\"]").setValue("transcript-markdown-de");
		await w.find(run).trigger("click");
		await flushPromises();
		// Only the request: the job fields belong to the processor.
		expect(store.updateTranscript).toHaveBeenCalledWith("t-1", {
			postprocess_status: "pending",
			postprocess_profile: "transcript-markdown-de",
		});
		expect(w.emitted("changed")).toHaveLength(1);
	});

	it("preselects the profile last run on the transcript", async () => {
		const w = await mount(transcript({ postprocess_profile: "transcript-polish-de", postprocess_status: "error" }));
		expect((w.find("[data-testid=\"postprocess-profile\"]").element as HTMLSelectElement).value).toBe("transcript-polish-de");
	});

	it("is unavailable with a reason while blocked (e.g. unsaved edits)", async () => {
		const w = await mount(transcript(), "Erst speichern oder verwerfen, dann nachbearbeiten.");
		expect(w.find(run).attributes("disabled")).toBeDefined();
		expect(w.find("[data-testid=\"postprocess-blocked\"]").text()).toContain("Erst speichern");
	});

	it("asks for a hub token instead of loading profiles when the device has none", async () => {
		hub.token = false;
		const w = await mount(transcript());
		expect(hub.list).not.toHaveBeenCalled();
		expect(w.find(run).attributes("disabled")).toBeDefined();
		expect(w.find("[data-testid=\"postprocess-blocked\"]").text()).toContain("Kein Hub-Token");
	});

	it("shows why the profile list failed instead of offering the fallback profile", async () => {
		hub.error = "Server nicht erreichbar.";
		const w = await mount(transcript());
		expect(w.find(run).attributes("disabled")).toBeDefined();
		expect(w.find("[data-testid=\"postprocess-profile\"]").attributes("disabled")).toBeDefined();
		expect(w.find("[data-testid=\"postprocess-blocked\"]").text()).toBe("Profile nicht verfügbar: Server nicht erreichbar.");
	});

	it("shows a running job and why it waits when no capable worker is online", async () => {
		const w = await mount(transcript({
			postprocess_status: "processing",
			postprocess_profile: "transcript-polish-de",
			postprocess_queue_reason: "no_capable_worker",
		}));
		expect(w.find("[data-testid=\"postprocess-running\"]").text()).toContain("„transcript-polish-de“");
		expect(w.find("[data-testid=\"postprocess-queue\"]").text()).toContain("Kein passender Worker online");
		expect(w.find(run).attributes("disabled")).toBeDefined();
		expect(w.find(restore).exists()).toBe(false);
	});

	it("shows the error of a failed run (e.g. policy_denied)", async () => {
		const w = await mount(transcript({
			postprocess_status: "error",
			postprocess_error: "Von der Richtlinie der Organisation abgelehnt: endpoint not assigned",
		}));
		expect(w.find("[data-testid=\"postprocess-error\"]").text()).toBe("Von der Richtlinie der Organisation abgelehnt: endpoint not assigned");
		expect(w.find(run).attributes("disabled")).toBeUndefined();
	});

	it("keeps the raw transcript visible after a run and restores it without asking when unedited", async () => {
		const confirm = vi.fn(() => true);
		vi.stubGlobal("confirm", confirm);
		const w = await mount(processed());
		const raw = w.find("[data-testid=\"postprocess-raw\"]");
		expect(raw.find("summary").text()).toContain("„transcript-markdown-de“");
		expect(raw.find("pre").text()).toBe(RAW);
		await w.find(restore).trigger("click");
		await flushPromises();
		expect(confirm).not.toHaveBeenCalled();
		expect(store.updateTranscript).toHaveBeenCalledWith("t-1", {
			text: RAW, raw_text: null, applied_profile: null, processed_text: null, postprocess_status: null,
		});
		expect(w.emitted("changed")).toHaveLength(1);
	});

	it("asks before a re-run or restore replaces edits made to the profile's output", async () => {
		const confirm = vi.fn(() => false);
		vi.stubGlobal("confirm", confirm);
		const w = await mount(processed({ text: `${PROCESSED} – ergänzt` }));
		await w.find(run).trigger("click");
		await w.find(restore).trigger("click");
		await flushPromises();
		expect(confirm).toHaveBeenCalledTimes(2);
		expect(store.updateTranscript).not.toHaveBeenCalled();

		confirm.mockReturnValue(true);
		await w.find(run).trigger("click");
		await flushPromises();
		expect(store.updateTranscript).toHaveBeenCalledWith("t-1", {
			postprocess_status: "pending", postprocess_profile: "transcript-markdown-de",
		});
	});

	it("reports a failed request write and stays put", async () => {
		store.updateTranscript.mockRejectedValueOnce(new Error("offline"));
		const w = await mount(transcript());
		await w.find(run).trigger("click");
		await flushPromises();
		expect(w.find("[data-testid=\"postprocess-action-error\"]").exists()).toBe(true);
		expect(w.emitted("changed")).toBeUndefined();
	});
});
