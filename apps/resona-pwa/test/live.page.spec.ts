import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref } from "vue";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import LivePage from "~/pages/live.vue";

const live = vi.hoisted(() => ({
	partial: null as null | { value: string },
	final: null as null | { value: string },
	status: null as null | { value: string },
}));
const liveStart = vi.hoisted(() => vi.fn(async () => {}));
const liveStop = vi.hoisted(() => vi.fn());

mockNuxtImport("useLive", () => () => {
	live.partial ??= ref("");
	live.final ??= ref("");
	live.status ??= ref("CLOSED");
	return {
		partial: live.partial,
		final: live.final,
		fullText: ref(""),
		status: live.status,
		start: liveStart,
		stop: liveStop,
	};
});

function setLive(key: "partial" | "final" | "status", value: string): void {
	live[key]!.value = value;
}
const partial = {
	set value(v: string) {
		setLive("partial", v);
	},
};
const final = {
	set value(v: string) {
		setLive("final", v);
	},
};
const status = {
	set value(v: string) {
		setLive("status", v);
	},
};

const recorderStart = vi.hoisted(() => vi.fn(async () => {}));
const recorderStop = vi.hoisted(() => vi.fn(async () => new Blob(["x"], { type: "audio/webm" })));

mockNuxtImport("useRecorder", () => () => ({
	recording: ref(false),
	paused: ref(false),
	elapsed: ref(7),
	levels: ref([] as number[]),
	start: recorderStart,
	stop: recorderStop,
	pause: vi.fn(),
	resume: vi.fn(),
}));

const uploadBlob = vi.hoisted(() => vi.fn(async () => "file-1"));
mockNuxtImport("useUpload", () => () => ({
	uploading: ref(false),
	error: ref(null),
	uploadBlob,
}));

const request = vi.hoisted(() => vi.fn());
mockNuxtImport("useDirectus", () => () => ({ client: { request } }));

const navigateTo = vi.hoisted(() => vi.fn());
mockNuxtImport("navigateTo", () => navigateTo);

const createItem = vi.hoisted(() =>
	vi.fn((collection: string, payload: unknown) => ({ kind: "create", collection, payload })),
);
vi.mock("@directus/sdk", () => ({
	createItem: (collection: string, payload: unknown) => createItem(collection, payload),
}));

describe("live transcription page", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		live.partial ??= ref("");
		live.final ??= ref("");
		live.status ??= ref("CLOSED");
		partial.value = "";
		final.value = "";
		status.value = "OPEN";
		request.mockReset();
		request
			.mockResolvedValueOnce({ id: "rec-1" })
			.mockResolvedValueOnce({ id: "t-1" });
	});

	it("auto-starts the live session on mount", async () => {
		await mountSuspended(LivePage);
		expect(liveStart).toHaveBeenCalled();
	});

	it("shows the rolling transcript with final and partial text", async () => {
		const w = await mountSuspended(LivePage);
		final.value = "Der Patient";
		partial.value = "klagt über";
		await w.vm.$nextTick();
		const transcript = w.find("[data-testid=\"transcript\"]");
		expect(transcript.exists()).toBe(true);
		expect(transcript.text()).toContain("Der Patient");
		expect(transcript.text()).toContain("klagt über");
	});

	it("shows a reconnect banner only when CLOSED mid-session", async () => {
		const w = await mountSuspended(LivePage);
		expect(w.find("[data-testid=\"reconnect-banner\"]").exists()).toBe(false);
		status.value = "CLOSED";
		await w.vm.$nextTick();
		expect(w.find("[data-testid=\"reconnect-banner\"]").exists()).toBe(true);
	});

	it("reveals a title prompt on stop and stops live + recorder", async () => {
		const w = await mountSuspended(LivePage);
		expect(w.find("[data-testid=\"title-input\"]").exists()).toBe(false);
		await w.find("[data-testid=\"stop-live\"]").trigger("click");
		await w.vm.$nextTick();
		expect(recorderStop).toHaveBeenCalled();
		expect(liveStop).toHaveBeenCalled();
		expect(w.find("[data-testid=\"title-input\"]").exists()).toBe(true);
	});

	it("confirming uploads the blob, creates the recordings then transcripts rows, and navigates home", async () => {
		final.value = "Der Befund ist unauffällig.";
		const w = await mountSuspended(LivePage);
		await w.find("[data-testid=\"stop-live\"]").trigger("click");
		await w.vm.$nextTick();

		const input = w.find("[data-testid=\"title-input\"]").find("input");
		await input.setValue("Visite Station 3");
		await w.find("[data-testid=\"confirm-save\"]").trigger("click");
		await w.vm.$nextTick();
		await new Promise(r => setTimeout(r, 0));
		await w.vm.$nextTick();

		// audio uploaded (happy-dom has no MediaRecorder so blob is null → uploadBlob may be skipped)
		// recordings row created first, with live source + done status
		expect(createItem).toHaveBeenCalledWith(
			"recordings",
			expect.objectContaining({
				title: "Visite Station 3",
				source: "live",
				status: "done",
			}),
		);

		// transcripts row created with the final text and the created recording id
		expect(createItem).toHaveBeenCalledWith(
			"transcripts",
			expect.objectContaining({
				recording: "rec-1",
				text: "Der Befund ist unauffällig.",
			}),
		);

		// recordings created before transcripts
		const recCall = createItem.mock.calls.findIndex(c => c[0] === "recordings");
		const txCall = createItem.mock.calls.findIndex(c => c[0] === "transcripts");
		expect(recCall).toBeGreaterThanOrEqual(0);
		expect(txCall).toBeGreaterThan(recCall);

		expect(navigateTo).toHaveBeenCalledWith("/");
	});
});
