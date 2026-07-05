import { describe, it, expect, vi, beforeEach } from "vitest";
import { h, ref } from "vue";
import { mountSuspended, mockNuxtImport, mockComponent } from "@nuxt/test-utils/runtime";
import RecordingPage from "~/pages/recording/[id].vue";
import type { Recording, Transcript } from "~/types/directus";

// ProseMirror/TipTap doesn't render meaningfully under happy-dom, so the page's
// editor is stubbed with a plain textarea that honours the same v-model +
// insertText/insertPlaceholder/replaceBetween contract the page relies on.
mockComponent("TranscriptEditor", () => ({
	props: ["modelValue", "placeholder"],
	emits: ["update:modelValue"],
	setup(props: { modelValue?: string }, { emit, expose }: { emit: (e: string, v: string) => void; expose: (api: unknown) => void }) {
		expose({
			insertText: (text: string) => {
				const cur = props.modelValue ?? "";
				const lead = cur.length > 0 && !/\s$/.test(cur) ? " " : "";
				emit("update:modelValue", cur + lead + text);
			},
			insertPlaceholder: () => {
				const cur = props.modelValue ?? "";
				const from = cur.length;
				emit("update:modelValue", cur + "[…]");
				return { from, to: from + 3 };
			},
			replaceBetween: (from: number, to: number, text: string) => {
				const cur = props.modelValue ?? "";
				emit("update:modelValue", cur.slice(0, from) + text + cur.slice(to));
			},
			focus: () => {},
		});
		return () => h("textarea", {
			"data-testid": "transcript-edit",
			"value": props.modelValue ?? "",
			"onInput": (e: Event) => emit("update:modelValue", (e.target as HTMLTextAreaElement).value),
		});
	},
}));

const recording = vi.hoisted(() => ({ value: null as Recording | null }));
const transcript = vi.hoisted(() => ({ value: null as Transcript | null }));
const loading = vi.hoisted(() => ({ value: false }));
const error = vi.hoisted(() => ({ value: null as string | null }));
const refresh = vi.hoisted(() => vi.fn(async () => {}));
const startPolling = vi.hoisted(() => vi.fn());
const stopPolling = vi.hoisted(() => vi.fn());

mockNuxtImport("useRecording", () => () => ({
	recording: ref(recording.value),
	transcript: ref(transcript.value),
	loading: ref(loading.value),
	error: ref(error.value),
	refresh,
	startPolling,
	stopPolling,
}));

const assetUrl = vi.hoisted(() => vi.fn((id: string) => `http://directus/assets/${id}`));
const request = vi.hoisted(() => vi.fn(async () => {}));

mockNuxtImport("useDirectus", () => () => ({
	client: { request },
	assetUrl,
}));

const navigateTo = vi.hoisted(() => vi.fn());
mockNuxtImport("navigateTo", () => navigateTo);

mockNuxtImport("useRoute", () => () => ({ params: { id: "rec-1" } }));

const deleteItem = vi.hoisted(() => vi.fn((collection: string, id: string) => ({ collection, id })));
const updateItem = vi.hoisted(() => vi.fn((collection: string, id: string, payload: unknown) => ({ kind: "update", collection, id, payload })));
const createItem = vi.hoisted(() => vi.fn((collection: string, payload: unknown) => ({ kind: "create", collection, payload })));
vi.mock("@directus/sdk", () => ({ deleteItem, updateItem, createItem }));

const recorderStart = vi.hoisted(() => vi.fn(async () => {}));
const recorderStop = vi.hoisted(() => vi.fn(async () => new Blob(["audio"], { type: "audio/webm;codecs=opus" })));

mockNuxtImport("useRecorder", () => () => ({
	recording: ref(false),
	paused: ref(false),
	elapsed: ref(0),
	levels: ref([] as number[]),
	start: recorderStart,
	stop: recorderStop,
	pause: vi.fn(),
	resume: vi.fn(),
}));

function baseRecording(over: Partial<Recording> = {}): Recording {
	return {
		id: "rec-1",
		title: "Visite",
		audio_file: "file-1",
		language: "de",
		profile: "default",
		status: "done",
		source: "batch",
		...over,
	};
}

describe("recording detail page", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		recording.value = baseRecording();
		transcript.value = { id: "t-1", recording: "rec-1", text: "Hallo Welt" };
		loading.value = false;
		error.value = null;
	});

	it("renders the transcript text when done", async () => {
		const w = await mountSuspended(RecordingPage);
		const area = w.find("[data-testid=\"transcript-edit\"]").element as HTMLTextAreaElement;
		expect(area.value).toContain("Hallo Welt");
	});

	it("starts polling on mount", async () => {
		await mountSuspended(RecordingPage);
		expect(startPolling).toHaveBeenCalled();
	});

	it("renders the player dock with the asset URL when audio_file is set", async () => {
		const w = await mountSuspended(RecordingPage);
		const dock = w.findComponent({ name: "HPlayerDock" });
		expect(dock.exists()).toBe(true);
		expect(dock.props("src")).toBe("http://directus/assets/file-1");
		expect(assetUrl).toHaveBeenCalledWith("file-1");
	});

	it("does not render the player dock when audio_file is missing", async () => {
		recording.value = baseRecording({ audio_file: undefined });
		const w = await mountSuspended(RecordingPage);
		expect(w.findComponent({ name: "HPlayerDock" }).exists()).toBe(false);
	});

	it("shows a placeholder and not the transcript while pending", async () => {
		recording.value = baseRecording({ status: "pending" });
		transcript.value = null;
		const w = await mountSuspended(RecordingPage);
		expect(w.text()).toContain("Transkribiert…");
		expect(w.text()).not.toContain("Hallo Welt");
	});

	it("shows the error message when status is error", async () => {
		recording.value = baseRecording({ status: "error", error_message: "Boom kaputt" });
		transcript.value = null;
		const w = await mountSuspended(RecordingPage);
		expect(w.text()).toContain("Boom kaputt");
	});

	it("delete asks for confirmation then deletes and navigates home", async () => {
		const w = await mountSuspended(RecordingPage);
		await w.find("[data-testid=\"delete\"]").trigger("click");
		expect(w.find("[data-testid=\"confirm-delete\"]").exists()).toBe(true);
		expect(request).not.toHaveBeenCalled();

		await w.find("[data-testid=\"confirm-delete\"]").trigger("click");
		await w.vm.$nextTick();
		expect(deleteItem).toHaveBeenCalledWith("recordings", "rec-1");
		expect(request).toHaveBeenCalled();
		expect(navigateTo).toHaveBeenCalledWith("/");
	});

	it("cancel hides the confirm UI without deleting", async () => {
		const w = await mountSuspended(RecordingPage);
		await w.find("[data-testid=\"delete\"]").trigger("click");
		await w.find("[data-testid=\"cancel-delete\"]").trigger("click");
		expect(w.find("[data-testid=\"confirm-delete\"]").exists()).toBe(false);
		expect(request).not.toHaveBeenCalled();
	});

	it("shows an empty editor for a done recording without a transcript", async () => {
		recording.value = baseRecording({ audio_file: undefined });
		transcript.value = null;
		const w = await mountSuspended(RecordingPage);
		const area = w.find("[data-testid=\"transcript-edit\"]");
		expect(area.exists()).toBe(true);
		expect((area.element as HTMLTextAreaElement).value).toBe("");
	});

	it("creates a transcript row when saving a recording that has none", async () => {
		recording.value = baseRecording({ audio_file: undefined });
		transcript.value = null;
		request.mockResolvedValueOnce({ id: "t-new", recording: "rec-1", text: "Manuell getippt" });
		const w = await mountSuspended(RecordingPage);

		await w.find("[data-testid=\"transcript-edit\"]").setValue("Manuell getippt");
		await w.find("[data-testid=\"save-transcript\"]").trigger("click");
		await w.vm.$nextTick();

		expect(createItem).toHaveBeenCalledWith("transcripts", expect.objectContaining({
			recording: "rec-1",
			text: "Manuell getippt",
		}));
		expect(request).toHaveBeenCalled();
	});

	it("still shows the editor on error so the user can write manually", async () => {
		recording.value = baseRecording({ status: "error", error_message: "Boom" });
		transcript.value = null;
		const w = await mountSuspended(RecordingPage);
		expect(w.find("[data-testid=\"transcript-edit\"]").exists()).toBe(true);
	});

	it("marks the review status ready via updateItem on recordings", async () => {
		const w = await mountSuspended(RecordingPage);
		await w.find("[data-testid=\"review-ready\"]").trigger("click");
		await w.vm.$nextTick();
		expect(updateItem).toHaveBeenCalledWith("recordings", "rec-1", { review_status: "ready" });
	});

	it("push-to-talk: inserts placeholder on press, records, transcribes via REST, replaces placeholder", async () => {
		globalThis.fetch = vi.fn(async () => ({
			ok: true,
			json: async () => ({ text: "diktierter satz" }),
		})) as unknown as typeof fetch;

		const w = await mountSuspended(RecordingPage);

		const ptt = w.find("[data-testid=\"push-to-talk\"]");
		await ptt.trigger("pointerdown", { pointerId: 1 });
		await w.vm.$nextTick();
		// Recorder starts
		expect(recorderStart).toHaveBeenCalled();
		// Placeholder inserted into editor
		const areaAfter = w.find("[data-testid=\"transcript-edit\"]").element as HTMLTextAreaElement;
		expect(areaAfter.value).toContain("[…]");

		await ptt.trigger("pointerup", { pointerId: 1 });
		await w.vm.$nextTick();
		await new Promise(r => setTimeout(r, 0));
		await w.vm.$nextTick();

		expect(recorderStop).toHaveBeenCalled();
		expect(globalThis.fetch).toHaveBeenCalledWith(
			expect.stringContaining("/v1/audio/transcriptions"),
			expect.objectContaining({ method: "POST" }),
		);
		// Placeholder replaced with transcribed text
		const areaFinal = w.find("[data-testid=\"transcript-edit\"]").element as HTMLTextAreaElement;
		expect(areaFinal.value).toContain("diktierter satz");
		expect(areaFinal.value).toContain("Hallo Welt");
		expect(areaFinal.value).not.toContain("[…]");
	});
});
