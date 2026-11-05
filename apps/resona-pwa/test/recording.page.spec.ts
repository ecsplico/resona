import { describe, it, expect, vi, beforeEach } from "vitest";
import { computed, h, nextTick, ref, type Ref } from "vue";
import type { VueWrapper } from "@vue/test-utils";
import { mountSuspended, mockNuxtImport, mockComponent } from "@nuxt/test-utils/runtime";
import RecordingPage from "~/pages/recording/[id].vue";
import type { Recording, Transcript } from "~/types/directus";
import type { ContinuePhase, ContinueResult } from "~/composables/useContinueRecording";
import { useHubSettings } from "~/composables/useHubSettings";
import { usePttDiagnostics } from "~/composables/usePttDiagnostics";
import { useRecordingsRefresh } from "~/composables/useShell";

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

// The page's live recording ref, so specs can simulate a polling update.
const live = vi.hoisted(() => ({ recording: null as Ref<Recording | null> | null }));

mockNuxtImport("useRecording", () => () => ({
	recording: (live.recording = ref(recording.value)),
	transcript: ref(transcript.value),
	loading: ref(loading.value),
	error: ref(error.value),
	refresh,
	startPolling,
	stopPolling,
}));

const store = vi.hoisted(() => ({
	audioUrl: vi.fn(async (r: { audio_file?: string | null }) => (r.audio_file ? `http://directus/assets/${r.audio_file}` : null)),
	updateRecording: vi.fn(async () => {}),
	deleteRecording: vi.fn(async () => {}),
	createTranscript: vi.fn(async () => ({ id: "t-new", recording: "rec-1", text: "" })),
	updateTranscript: vi.fn(async () => {}),
}));
mockNuxtImport("useDataStore", () => () => store);

const navigateTo = vi.hoisted(() => vi.fn());
mockNuxtImport("navigateTo", () => navigateTo);

mockNuxtImport("useRoute", () => () => ({ params: { id: "rec-1" } }));

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

const exporter = vi.hoisted(() => ({
	toText: vi.fn(() => "text"),
	copy: vi.fn(async () => {}),
	downloadText: vi.fn(),
	downloadMarkdown: vi.fn(),
}));
mockNuxtImport("useExport", () => () => exporter);

// Continue-recording engine: specs drive the phase/error refs and resolve
// finish/retry with a result; the page reacts like it would to the real engine.
const cont = vi.hoisted(() => ({
	phase: null as Ref<ContinuePhase> | null,
	error: null as Ref<string | null> | null,
	retained: null as Ref<boolean> | null,
	start: vi.fn(async () => {}),
	cancel: vi.fn(async () => {}),
	finish: vi.fn(async (): Promise<ContinueResult | null> => null),
	retry: vi.fn(async (): Promise<ContinueResult | null> => null),
	discard: vi.fn(),
}));

mockNuxtImport("useContinueRecording", () => () => {
	cont.phase = ref<ContinuePhase>("idle");
	cont.error = ref<string | null>(null);
	cont.retained = ref(false);
	const retained = cont.retained;
	return {
		recorder: {
			recording: ref(false),
			paused: ref(false),
			elapsed: ref(0),
			levels: ref([] as number[]),
			start: vi.fn(),
			stop: vi.fn(),
			pause: vi.fn(),
			resume: vi.fn(),
		},
		phase: cont.phase,
		error: cont.error,
		hasRetainedPart: computed(() => retained.value),
		start: cont.start,
		cancel: cont.cancel,
		finish: cont.finish,
		retry: cont.retry,
		discard: cont.discard,
	};
});

function continueResult(over: Partial<ContinueResult> = {}): ContinueResult {
	return { text: "Neuer Teil", audio_file: "file-2", duration_seconds: 70, audioMerged: true, mergeError: null, ...over };
}

// Record a part through the sheet: start → phase recording → stop → finish.
async function recordPart(w: VueWrapper): Promise<void> {
	await w.find("[data-testid=\"continue-recording\"]").trigger("click");
	expect(cont.start).toHaveBeenCalledTimes(1);
	cont.phase!.value = "recording";
	await nextTick();
	expect(w.find("#h-rec-title").text()).toBe("Weiter aufnehmen");
	await w.find("[data-testid=\"stop\"]").trigger("click");
}

// How often the page asked the (desktop) recordings list to reload.
function listReloads(): number {
	return useRecordingsRefresh().signal.value;
}

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

	it("renders the player dock with the store's audio URL when audio_file is set", async () => {
		const w = await mountSuspended(RecordingPage);
		await vi.waitFor(() => expect(w.findComponent({ name: "HPlayerDock" }).exists()).toBe(true));
		const dock = w.findComponent({ name: "HPlayerDock" });
		expect(dock.props("src")).toBe("http://directus/assets/file-1");
		expect(store.audioUrl).toHaveBeenCalledWith(expect.objectContaining({ id: "rec-1", audio_file: "file-1" }));
	});

	it("does not re-resolve the audio URL when a poll returns the same audio_file", async () => {
		const w = await mountSuspended(RecordingPage);
		await vi.waitFor(() => expect(w.findComponent({ name: "HPlayerDock" }).exists()).toBe(true));
		live.recording!.value = baseRecording({ review_status: "ready" });
		await nextTick();
		await nextTick();
		expect(store.audioUrl).toHaveBeenCalledTimes(1);
	});

	it("revokes a local blob: audio URL on unmount", async () => {
		store.audioUrl.mockResolvedValueOnce("blob:local-1");
		const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
		const w = await mountSuspended(RecordingPage);
		await vi.waitFor(() => expect(w.findComponent({ name: "HPlayerDock" }).exists()).toBe(true));
		w.unmount();
		expect(revoke).toHaveBeenCalledWith("blob:local-1");
		revoke.mockRestore();
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

	it("delete asks for confirmation then deletes, reloads the list and navigates home", async () => {
		const w = await mountSuspended(RecordingPage);
		await w.find("[data-testid=\"delete\"]").trigger("click");
		expect(w.find("[data-testid=\"confirm-delete\"]").exists()).toBe(true);
		expect(store.deleteRecording).not.toHaveBeenCalled();

		const reloads = listReloads();
		await w.find("[data-testid=\"confirm-delete\"]").trigger("click");
		await w.vm.$nextTick();
		expect(store.deleteRecording).toHaveBeenCalledWith("rec-1");
		expect(navigateTo).toHaveBeenCalledWith("/");
		expect(listReloads()).toBe(reloads + 1);
	});

	it("Neu transkribieren resets the recording to pending, reloads the list and polls", async () => {
		recording.value = baseRecording({ status: "error", error_message: "Boom" });
		transcript.value = null;
		const w = await mountSuspended(RecordingPage);
		startPolling.mockClear();
		const reloads = listReloads();
		await w.find("[data-testid=\"restart-transcription\"]").trigger("click");
		await vi.waitFor(() => expect(listReloads()).toBe(reloads + 1));
		expect(store.updateRecording).toHaveBeenCalledWith("rec-1", { status: "pending", error_message: null });
		expect(startPolling).toHaveBeenCalledOnce();
	});

	it("cancel hides the confirm UI without deleting", async () => {
		const w = await mountSuspended(RecordingPage);
		await w.find("[data-testid=\"delete\"]").trigger("click");
		await w.find("[data-testid=\"cancel-delete\"]").trigger("click");
		expect(w.find("[data-testid=\"confirm-delete\"]").exists()).toBe(false);
		expect(store.deleteRecording).not.toHaveBeenCalled();
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
		store.createTranscript.mockResolvedValueOnce({ id: "t-new", recording: "rec-1", text: "Manuell getippt" });
		const w = await mountSuspended(RecordingPage);

		await w.find("[data-testid=\"transcript-edit\"]").setValue("Manuell getippt");
		await w.find("[data-testid=\"save-transcript\"]").trigger("click");
		await w.vm.$nextTick();

		expect(store.createTranscript).toHaveBeenCalledWith("rec-1", expect.objectContaining({ text: "Manuell getippt" }));
	});

	it("still shows the editor on error so the user can write manually", async () => {
		recording.value = baseRecording({ status: "error", error_message: "Boom" });
		transcript.value = null;
		const w = await mountSuspended(RecordingPage);
		expect(w.find("[data-testid=\"transcript-edit\"]").exists()).toBe(true);
	});

	it("review switch is a radiogroup that sets the review state via the data store", async () => {
		const w = await mountSuspended(RecordingPage);
		expect(w.find("[role=\"radiogroup\"]").exists()).toBe(true);
		const editing = w.find("[data-testid=\"review-editing\"]");
		const ready = w.find("[data-testid=\"review-ready\"]");
		expect(editing.attributes("aria-checked")).toBe("true");
		expect(ready.text()).toBe("Freigegeben");
		expect(w.text()).not.toContain("Fertig");
		await ready.trigger("click");
		await vi.waitFor(() => expect(store.updateRecording).toHaveBeenCalledWith("rec-1", { review_status: "ready" }));
		await nextTick();
		expect(w.find("[data-testid=\"review-ready\"]").attributes("aria-checked")).toBe("true");
	});

	it("export menu opens and both items export, Escape closes it", async () => {
		const w = await mountSuspended(RecordingPage, { attachTo: document.body });
		expect(w.find("[data-testid=\"export-txt\"]").exists()).toBe(false);
		await w.find("[data-testid=\"export-menu\"]").trigger("click");
		await w.find("[data-testid=\"export-txt\"]").trigger("click");
		expect(exporter.downloadText).toHaveBeenCalledWith(expect.objectContaining({ id: "rec-1" }), expect.objectContaining({ text: "Hallo Welt" }));
		expect(w.find("[data-testid=\"export-txt\"]").exists()).toBe(false);

		await w.find("[data-testid=\"export-menu\"]").trigger("click");
		await w.find("[data-testid=\"export-md\"]").trigger("click");
		expect(exporter.downloadMarkdown).toHaveBeenCalledTimes(1);

		await w.find("[data-testid=\"export-menu\"]").trigger("click");
		await w.find("[role=\"menu\"]").trigger("keydown", { key: "Escape" });
		expect(w.find("[role=\"menu\"]").exists()).toBe(false);
		expect(document.activeElement).toBe(w.find("[data-testid=\"export-menu\"]").element);
		w.unmount();
	});

	it("save button is disabled when clean and enabled when dirty", async () => {
		const w = await mountSuspended(RecordingPage);
		const save = () => w.find("[data-testid=\"save-transcript\"]");
		expect(save().attributes("disabled")).toBeDefined();
		expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Gespeichert");
		await w.find("[data-testid=\"transcript-edit\"]").setValue("Hallo Welt, geändert");
		expect(save().attributes("disabled")).toBeUndefined();
		expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Ungespeicherte Änderungen");
	});

	it("shows the back button and no state chip for a transcribed recording on phones", async () => {
		const w = await mountSuspended(RecordingPage);
		expect(w.find("[data-testid=\"back\"]").exists()).toBe(true);
		expect(w.text()).not.toContain("Transkribiert…");
	});

	it("push-to-talk: inserts placeholder on press, records, transcribes via REST, replaces placeholder", async () => {
		useHubSettings().token = "tok";
		globalThis.fetch = vi.fn(async () => new Response(
			JSON.stringify({ text: "diktierter satz" }),
			{ status: 200, headers: { "Content-Type": "application/json" } },
		)) as unknown as typeof fetch;

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
			expect.objectContaining({
				method: "POST",
				headers: expect.objectContaining({ Authorization: "Bearer tok" }),
			}),
		);
		// Placeholder replaced with transcribed text
		await vi.waitFor(() => {
			const area = w.find("[data-testid=\"transcript-edit\"]").element as HTMLTextAreaElement;
			expect(area.value).toContain("diktierter satz");
		});
		const areaFinal = w.find("[data-testid=\"transcript-edit\"]").element as HTMLTextAreaElement;
		expect(areaFinal.value).toContain("Hallo Welt");
		expect(areaFinal.value).not.toContain("[…]");
	});

	it("push-to-talk without a hub token records the diagnostic and never calls the hub", async () => {
		useHubSettings().token = "";
		globalThis.fetch = vi.fn() as unknown as typeof fetch;
		const w = await mountSuspended(RecordingPage);
		const ptt = w.find("[data-testid=\"push-to-talk\"]");
		await ptt.trigger("pointerdown", { pointerId: 1 });
		await w.vm.$nextTick();
		await ptt.trigger("pointerup", { pointerId: 1 });
		await w.vm.$nextTick();
		await new Promise(r => setTimeout(r, 0));
		await w.vm.$nextTick();
		expect(globalThis.fetch).not.toHaveBeenCalled();
		const fail = usePttDiagnostics().entries.value.find(e => !e.ok);
		expect(fail?.detail).toBe("Kein Hub-Token – in Einstellungen → Verbindung eintragen");
	});

	describe("continue recording", () => {
		beforeEach(() => {
			cont.finish.mockImplementation(async () => {
				cont.phase!.value = "idle";
				return continueResult();
			});
		});

		it("appends the new part as a paragraph after unsaved edits and saves once", async () => {
			const w = await mountSuspended(RecordingPage);
			await w.find("[data-testid=\"transcript-edit\"]").setValue("Hallo Welt, geändert");
			await recordPart(w);

			await vi.waitFor(() => expect(store.updateTranscript).toHaveBeenCalledTimes(1));
			expect(cont.finish).toHaveBeenCalledWith(expect.objectContaining({ id: "rec-1" }));
			expect(store.updateTranscript).toHaveBeenCalledWith("t-1", { text: "Hallo Welt, geändert\nNeuer Teil" });
			const area = w.find("[data-testid=\"transcript-edit\"]").element as HTMLTextAreaElement;
			expect(area.value).toBe("Hallo Welt, geändert\nNeuer Teil");
			await nextTick();
			expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Gespeichert");
			expect(w.find("[data-testid=\"continue-notice\"]").exists()).toBe(false);
		});

		it("adds no separator when the text already ends with a newline", async () => {
			transcript.value = { id: "t-1", recording: "rec-1", text: "Erster Absatz\n" };
			const w = await mountSuspended(RecordingPage);
			await recordPart(w);
			await vi.waitFor(() => expect(store.updateTranscript).toHaveBeenCalledWith("t-1", { text: "Erster Absatz\nNeuer Teil" }));
		});

		it("creates the transcript row with just the new text when there was none", async () => {
			transcript.value = null;
			store.createTranscript.mockResolvedValueOnce({ id: "t-new", recording: "rec-1", text: "Neuer Teil" });
			const w = await mountSuspended(RecordingPage);
			await recordPart(w);
			await vi.waitFor(() => expect(store.createTranscript).toHaveBeenCalledWith("rec-1", { text: "Neuer Teil" }));
			expect(store.createTranscript).toHaveBeenCalledTimes(1);
		});

		it("switches the player to the merged audio file", async () => {
			const w = await mountSuspended(RecordingPage);
			await vi.waitFor(() => expect(w.findComponent({ name: "HPlayerDock" }).exists()).toBe(true));
			await recordPart(w);
			await vi.waitFor(() =>
				expect(w.findComponent({ name: "HPlayerDock" }).props("src")).toBe("http://directus/assets/file-2"),
			);
			expect(w.findComponent({ name: "HPlayerDock" }).props("fallbackDuration")).toBe(70);
		});

		it("asks the recordings list to reload once the new part is saved", async () => {
			const w = await mountSuspended(RecordingPage);
			const reloads = listReloads();
			await recordPart(w);
			await vi.waitFor(() => expect(listReloads()).toBe(reloads + 1));
			expect(store.updateTranscript).toHaveBeenCalledWith("t-1", { text: "Hallo Welt\nNeuer Teil" });
		});

		it("leaves the list alone when the continuation was cancelled", async () => {
			cont.finish.mockImplementation(async () => {
				cont.phase!.value = "idle";
				return null;
			});
			const w = await mountSuspended(RecordingPage);
			const reloads = listReloads();
			await recordPart(w);
			await vi.waitFor(() => expect(cont.finish).toHaveBeenCalledOnce());
			await nextTick();
			expect(listReloads()).toBe(reloads);
			expect(store.updateTranscript).not.toHaveBeenCalled();
		});

		it("keeps the old audio and shows a notice when the merge failed", async () => {
			cont.finish.mockImplementation(async () => {
				cont.phase!.value = "idle";
				return continueResult({ audio_file: null, duration_seconds: 0, audioMerged: false, mergeError: "Decoder kaputt" });
			});
			const w = await mountSuspended(RecordingPage);
			await vi.waitFor(() => expect(w.findComponent({ name: "HPlayerDock" }).exists()).toBe(true));
			await recordPart(w);
			await vi.waitFor(() => expect(w.find("[data-testid=\"continue-notice\"]").exists()).toBe(true));
			expect(w.find("[data-testid=\"continue-notice\"]").text())
				.toBe("Text angehängt, Audio konnte nicht zusammengeführt werden: Decoder kaputt");
			expect(store.updateTranscript).toHaveBeenCalledWith("t-1", { text: "Hallo Welt\nNeuer Teil" });
			expect(w.findComponent({ name: "HPlayerDock" }).props("src")).toBe("http://directus/assets/file-1");
		});

		it("cancel in the sheet cancels the continuation", async () => {
			const w = await mountSuspended(RecordingPage);
			await w.find("[data-testid=\"continue-recording\"]").trigger("click");
			cont.phase!.value = "recording";
			await nextTick();
			await w.find("[data-testid=\"cancel\"]").trigger("click");
			expect(cont.cancel).toHaveBeenCalledTimes(1);
			expect(cont.finish).not.toHaveBeenCalled();
		});

		it("while transcribing shows the banner with Abbrechen and disables save, dictation and continue", async () => {
			const w = await mountSuspended(RecordingPage);
			await w.find("[data-testid=\"transcript-edit\"]").setValue("Hallo Welt, geändert");
			cont.phase!.value = "transcribing";
			await nextTick();

			const banner = w.find("[data-testid=\"continue-banner\"]");
			expect(banner.text()).toContain("Neuer Teil wird transkribiert – Seite bitte geöffnet lassen.");
			expect(w.find("[data-testid=\"continue-recording\"]").attributes("disabled")).toBeDefined();
			expect(w.find("[data-testid=\"save-transcript\"]").attributes("disabled")).toBeDefined();
			expect(w.find("[data-testid=\"push-to-talk\"]").attributes("disabled")).toBeDefined();
			expect(w.find("[data-testid=\"transcript-edit\"]").exists()).toBe(true);

			await w.find("[data-testid=\"continue-cancel\"]").trigger("click");
			expect(cont.cancel).toHaveBeenCalledTimes(1);
		});

		it("while merging shows the merge banner without Abbrechen", async () => {
			const w = await mountSuspended(RecordingPage);
			cont.phase!.value = "merging";
			await nextTick();
			expect(w.find("[data-testid=\"continue-banner\"]").text()).toContain("Audio wird zusammengeführt…");
			expect(w.find("[data-testid=\"continue-cancel\"]").exists()).toBe(false);
			expect(w.find("[data-testid=\"continue-recording\"]").attributes("disabled")).toBeDefined();
		});

		it("error banner retries without re-recording and then appends", async () => {
			cont.retry.mockImplementation(async () => {
				cont.phase!.value = "idle";
				cont.retained!.value = false;
				return continueResult({ text: "Nachgereicht" });
			});
			const w = await mountSuspended(RecordingPage);
			cont.error!.value = "Transkription fehlgeschlagen: Hub weg";
			cont.retained!.value = true;
			cont.phase!.value = "error";
			await nextTick();

			expect(w.find("[data-testid=\"continue-banner\"]").text()).toContain("Transkription fehlgeschlagen: Hub weg");
			expect(w.find("[data-testid=\"continue-recording\"]").attributes("disabled")).toBeDefined();
			await w.find("[data-testid=\"continue-retry\"]").trigger("click");

			expect(cont.retry).toHaveBeenCalledWith(expect.objectContaining({ id: "rec-1" }));
			expect(cont.start).not.toHaveBeenCalled();
			await vi.waitFor(() => expect(store.updateTranscript).toHaveBeenCalledWith("t-1", { text: "Hallo Welt\nNachgereicht" }));
			expect(w.find("[data-testid=\"continue-banner\"]").exists()).toBe(false);
		});

		it("error banner discard drops the part", async () => {
			const w = await mountSuspended(RecordingPage);
			cont.error!.value = "Kein Hub-Token";
			cont.retained!.value = true;
			cont.phase!.value = "error";
			await nextTick();
			await w.find("[data-testid=\"continue-discard\"]").trigger("click");
			expect(cont.discard).toHaveBeenCalledTimes(1);
			expect(store.updateTranscript).not.toHaveBeenCalled();
		});

		it("shows a microphone failure inline in the toolbar and allows another try", async () => {
			cont.start.mockImplementationOnce(async () => {
				cont.error!.value = "Mikrofon nicht verfügbar";
				cont.phase!.value = "error";
			});
			const w = await mountSuspended(RecordingPage);
			await w.find("[data-testid=\"continue-recording\"]").trigger("click");
			await nextTick();
			expect(w.find("[data-testid=\"continue-error\"]").text()).toBe("Mikrofon nicht verfügbar");
			expect(w.find("[data-testid=\"continue-banner\"]").exists()).toBe(false);
			expect(w.find("[data-testid=\"continue-recording\"]").attributes("disabled")).toBeUndefined();
		});

		it("is not offered while the recording is still transcribing", async () => {
			recording.value = baseRecording({ status: "pending" });
			transcript.value = null;
			const w = await mountSuspended(RecordingPage);
			expect(w.find("[data-testid=\"continue-recording\"]").exists()).toBe(false);
		});
	});
});
