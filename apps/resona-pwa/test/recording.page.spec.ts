import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { computed, h, nextTick, ref, type Ref } from "vue";
import { flushPromises, type VueWrapper } from "@vue/test-utils";
import { mountSuspended, mockNuxtImport, mockComponent } from "@nuxt/test-utils/runtime";
import RecordingPage from "~/pages/recording/[id].vue";
import type { Recording, Transcript } from "~/types/directus";
import type { RedictationAudio, RedictationHooks, RedictationPart } from "~/composables/useRedictation";
import { useHubSettings } from "~/composables/useHubSettings";
import { usePttDiagnostics } from "~/composables/usePttDiagnostics";
import { useRecordingsRefresh } from "~/composables/useShell";

// ProseMirror/TipTap doesn't render meaningfully under happy-dom, so the page's
// editor is stubbed with a plain textarea that honours the same v-model +
// insertText/appendParagraph/dictation-part contract the page relies on. A chip
// is kept as an offset into the text (moved along with typing before it), never
// in the value, and listed in `data-dictation-parts` as `<id>:<status>`.
mockComponent("TranscriptEditor", () => ({
	props: ["modelValue", "placeholder", "editable"],
	emits: ["update:modelValue", "typed"],
	setup(props: { modelValue?: string; editable?: boolean }, { emit, expose }: { emit: (e: string, v?: string) => void; expose: (api: unknown) => void }) {
		const parts = ref<{ partId: string; status: string; at: number }[]>([]);
		expose({
			insertText: (text: string) => {
				const cur = props.modelValue ?? "";
				const lead = cur.length > 0 && !/\s$/.test(cur) ? " " : "";
				emit("update:modelValue", cur + lead + text);
			},
			appendParagraph: (text: string) => {
				const cur = props.modelValue ?? "";
				const separator = cur.length === 0 || cur.endsWith("\n") ? "" : "\n";
				emit("update:modelValue", cur + separator + text);
			},
			focus: () => {},
			hasFocus: () => editorCaret.value !== null,
			insertDictationPart: (partId: string, at: "caret" | "end") => {
				const cur = props.modelValue ?? "";
				parts.value.push({ partId, status: "recording", at: at === "caret" && editorCaret.value !== null ? editorCaret.value : cur.length });
			},
			setDictationPartStatus: (partId: string, status: string) => {
				const part = parts.value.find(p => p.partId === partId);
				if (part) part.status = status;
			},
			removeDictationPart: (partId: string) => {
				parts.value = parts.value.filter(p => p.partId !== partId);
			},
			resolveDictationPart: (partId: string, text: string, options: { paragraph?: boolean } = {}) => {
				const part = parts.value.find(p => p.partId === partId);
				if (!part) return null;
				parts.value = parts.value.filter(p => p !== part);
				if (!text) return null;
				const base = props.modelValue ?? "";
				const { at } = part;
				let inserted: string;
				if (options.paragraph) {
					// Like the real editor: the text splits the paragraph the chip stands in.
					const before = at > 0 && base[at - 1] !== "\n";
					const after = at < base.length && base[at] !== "\n";
					inserted = (before ? "\n" : "") + text + (after ? "\n" : "");
				}
				else {
					const lead = at > 0 && !/\s/.test(base[at - 1]!) ? " " : "";
					const trail = at < base.length && /\s/.test(base[at]!) ? "" : " ";
					inserted = lead + text + trail;
				}
				emit("update:modelValue", base.slice(0, at) + inserted + base.slice(at));
				return { base, at, inserted };
			},
		});
		return () => h("textarea", {
			"data-testid": "transcript-edit",
			"data-dictation-parts": parts.value.map(p => `${p.partId}:${p.status}`).join(" "),
			"value": props.modelValue ?? "",
			"readonly": props.editable === false,
			// Input is the user typing; the exposed methods above are not.
			"onInput": (e: Event) => {
				const before = props.modelValue ?? "";
				const after = (e.target as HTMLTextAreaElement).value;
				// The one changed span; chips behind it move by the length change.
				let start = 0;
				while (start < before.length && start < after.length && before[start] === after[start]) start += 1;
				let end = 0;
				while (end < before.length - start && end < after.length - start && before[before.length - 1 - end] === after[after.length - 1 - end]) end += 1;
				for (const p of parts.value) {
					if (p.at >= before.length - end) p.at += after.length - before.length;
					else if (p.at > start) p.at = start;
				}
				emit("typed");
				emit("update:modelValue", after);
			},
		});
	},
}));

// The mock editor's caret: null = not focused (chips go to the end of the text).
const editorCaret = vi.hoisted(() => ({ value: null as number | null }));

const recording = vi.hoisted(() => ({ value: null as Recording | null }));
const transcript = vi.hoisted(() => ({ value: null as Transcript | null }));
const loading = vi.hoisted(() => ({ value: false }));
const error = vi.hoisted(() => ({ value: null as string | null }));
const refresh = vi.hoisted(() => vi.fn(async () => {}));
const startPolling = vi.hoisted(() => vi.fn());
const stopPolling = vi.hoisted(() => vi.fn());

// The page's live recording/transcript refs, so specs can simulate a polling update.
const live = vi.hoisted(() => ({
	recording: null as Ref<Recording | null> | null,
	transcript: null as Ref<Transcript | null> | null,
}));

mockNuxtImport("useRecording", () => () => ({
	recording: (live.recording = ref(recording.value)),
	transcript: (live.transcript = ref(transcript.value)),
	loading: ref(loading.value),
	error: ref(error.value),
	refresh,
	startPolling,
	stopPolling,
}));

mockNuxtImport("useProfiles", () => () => ({
	profiles: ref(["transcript-markdown-de"]),
	error: ref(null),
	list: vi.fn(async () => ["transcript-markdown-de"]),
}));

const store = vi.hoisted(() => ({
	audioUrl: vi.fn(async (r: { audio_file?: string | null }) => (r.audio_file ? `http://directus/assets/${r.audio_file}` : null)),
	updateRecording: vi.fn(async () => {}),
	deleteRecording: vi.fn(async () => {}),
	createTranscript: vi.fn(async () => ({ id: "t-new", recording: "rec-1", text: "" })),
	updateTranscript: vi.fn(async () => {}),
	recordCorrection: vi.fn(async () => {}),
}));
mockNuxtImport("useDataStore", () => () => store);

const navigateTo = vi.hoisted(() => vi.fn());
mockNuxtImport("navigateTo", () => navigateTo);

const nav = vi.hoisted(() => ({ back: vi.fn(async () => {}), leaveTo: vi.fn(async () => {}) }));
mockNuxtImport("useBackNav", () => () => nav);
mockNuxtImport("useRoute", () => () => ({ params: { id: "rec-1" } }));

// The page's route guards, so specs can run them like the router would.
type Guard = (to: { params: Record<string, string> }, from: { params: Record<string, string> }) => unknown;
const guards = vi.hoisted(() => ({ leave: null as Guard | null, update: null as Guard | null }));
mockNuxtImport("onBeforeRouteLeave", () => (guard: Guard) => {
	guards.leave = guard;
});
mockNuxtImport("onBeforeRouteUpdate", () => (guard: Guard) => {
	guards.update = guard;
});

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

// Re-dictation engine: the spies decide what start/finish return; the wrapper
// keeps the state like the real engine. Specs land or fail a part through the
// page's hooks (`landPart`, `failPart`) and report merged audio via `redict.hooks`.
const redict = vi.hoisted(() => ({
	hooks: null as RedictationHooks | null,
	recording: null as Ref<boolean> | null,
	startError: null as Ref<string | null> | null,
	parts: null as Ref<RedictationPart[]> | null,
	merging: null as Ref<boolean> | null,
	start: vi.fn(async (_partId: string) => true),
	cancel: vi.fn(async () => {}),
	finish: vi.fn(async () => true),
	retry: vi.fn((_partId: string) => {}),
	retryAudio: vi.fn((_partId: string) => {}),
	discard: vi.fn((_partId: string) => {}),
}));

mockNuxtImport("useRedictation", () => (hooks: RedictationHooks) => {
	redict.hooks = hooks;
	const recordingRef = (redict.recording = ref(false));
	const startError = (redict.startError = ref<string | null>(null));
	const parts = (redict.parts = ref<RedictationPart[]>([]));
	const merging = (redict.merging = ref(false));
	const transcribing = computed(() => parts.value.filter(p => p.status === "transcribing").length);
	let micPart: string | null = null;
	return {
		recorder: { elapsed: ref(0), levels: ref([] as number[]), paused: ref(false), pause: vi.fn(), resume: vi.fn() },
		recording: recordingRef,
		startError,
		parts,
		transcribing,
		merging: computed(() => merging.value),
		busy: computed(() => recordingRef.value || transcribing.value > 0 || merging.value),
		failed: computed(() => parts.value.filter(p => p.status === "error" || p.audio === "failed").length),
		start: async (partId: string) => {
			startError.value = null;
			const ok = await redict.start(partId);
			if (ok) {
				recordingRef.value = true;
				micPart = partId;
			}
			return ok;
		},
		cancel: async () => {
			recordingRef.value = false;
			micPart = null;
			await redict.cancel();
		},
		finish: async () => {
			const id = micPart;
			micPart = null;
			recordingRef.value = false;
			const ok = await redict.finish();
			if (ok && id) parts.value = [...parts.value, { id, status: "transcribing", error: null, audio: "saved", audioError: null }];
			return ok;
		},
		retry: (partId: string) => {
			redict.retry(partId);
			parts.value = parts.value.map(p => (p.id === partId ? { ...p, status: "transcribing", error: null } : p));
		},
		retryAudio: (partId: string) => {
			redict.retryAudio(partId);
			parts.value = parts.value.map(p => (p.id === partId ? { ...p, audio: "appending", audioError: null } : p));
		},
		discard: (partId: string) => {
			redict.discard(partId);
			parts.value = parts.value.filter(p => p.id !== partId);
		},
	};
});

// A part's hub job delivered its text.
async function landPart(partId: string, text: string): Promise<void> {
	redict.parts!.value = redict.parts!.value.filter(p => p.id !== partId);
	redict.hooks!.onText(partId, text);
	await flushPromises();
}

// A part's hub job failed.
async function failPart(partId: string, message: string): Promise<void> {
	redict.parts!.value = redict.parts!.value.map(p => (p.id === partId ? { ...p, status: "error", error: message } : p));
	redict.hooks!.onError(partId, message);
	await flushPromises();
}

function mergedAudio(partId: string, over: Partial<RedictationAudio> = {}): RedictationAudio {
	return { partId, ok: true, audio_file: "file-2", duration_seconds: 70, ...over } as RedictationAudio;
}

// Record one part through the record bar: open → stop. Returns its id.
async function recordPart(w: VueWrapper): Promise<string> {
	await w.find("[data-testid=\"continue-recording\"]").trigger("click");
	await flushPromises();
	expect(w.find("[data-testid=\"record-bar-label\"]").text()).toBe("Nachdiktat");
	await w.find("[data-testid=\"stop\"]").trigger("click");
	await flushPromises();
	return redict.parts!.value.at(-1)!.id;
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
		editorCaret.value = null;
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
		expect(nav.leaveTo).toHaveBeenCalledWith("/");
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

	it("confirming the unchanged placeholder title makes it the user's (title_auto false)", async () => {
		recording.value = baseRecording({ title: "Aufnahme 10.11.2026 09:05", title_auto: true });
		const w = await mountSuspended(RecordingPage);
		await w.find("[data-testid=\"bar-title\"]").trigger("click");
		await w.find("[data-testid=\"title-input\"]").trigger("keydown", { key: "Enter" });
		await flushPromises();
		expect(store.updateRecording).toHaveBeenCalledWith("rec-1", { title: "Aufnahme 10.11.2026 09:05", title_auto: false });
		expect(w.find("[data-testid=\"title-input\"]").exists()).toBe(false);
	});

	it("confirming an unchanged title of the user's own writes nothing", async () => {
		recording.value = baseRecording({ title: "Visite", title_auto: false });
		const w = await mountSuspended(RecordingPage);
		await w.find("[data-testid=\"bar-title\"]").trigger("click");
		await w.find("[data-testid=\"title-input\"]").trigger("keydown", { key: "Enter" });
		await flushPromises();
		expect(store.updateRecording).not.toHaveBeenCalled();
		expect(w.find("[data-testid=\"title-input\"]").exists()).toBe(false);
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

	it("Speichern shows Speichert… at once and a double click saves once", async () => {
		let finishWrite!: () => void;
		store.updateTranscript.mockImplementationOnce(() => new Promise<void>((resolve) => {
			finishWrite = resolve;
		}));
		const w = await mountSuspended(RecordingPage);
		const long = `Hallo Welt ${"und noch ein Satz ".repeat(3000)}`;
		await w.find("[data-testid=\"transcript-edit\"]").setValue(long);
		const save = w.find("[data-testid=\"save-transcript\"]");
		// Both clicks land before the page re-renders the button as disabled.
		(save.element as HTMLButtonElement).click();
		(save.element as HTMLButtonElement).click();
		await nextTick();
		expect(save.text()).toBe("Speichert…");
		expect(save.attributes("disabled")).toBeDefined();
		expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Speichert…");

		finishWrite();
		await flushPromises();
		expect(store.updateTranscript).toHaveBeenCalledTimes(1);
		expect(store.updateTranscript).toHaveBeenCalledWith("t-1", { text: long });
		expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Gespeichert");
		// The correction is stored once, after the save showed.
		await vi.waitFor(() => expect(store.recordCorrection).toHaveBeenCalledTimes(1));
	});

	describe("dictation is saved on its own", () => {
		const area = (w: VueWrapper) => w.find("[data-testid=\"transcript-edit\"]").element as HTMLTextAreaElement;
		const here = { params: { id: "rec-1" } };
		const elsewhere = { params: { id: "rec-2" } };

		beforeEach(() => {
			useHubSettings().token = "tok";
			globalThis.fetch = vi.fn(async () => new Response(
				JSON.stringify({ text: "diktiert" }),
				{ status: 200, headers: { "Content-Type": "application/json" } },
			)) as unknown as typeof fetch;
		});

		async function dictate(w: VueWrapper): Promise<void> {
			const ptt = w.find("[data-testid=\"push-to-talk\"]");
			await ptt.trigger("pointerdown", { pointerId: 1 });
			await flushPromises();
			heldFor(500);
			await ptt.trigger("pointerup", { pointerId: 1 });
			await vi.waitFor(() => expect(area(w).value).toContain("diktiert"));
			await flushPromises();
		}

		it("saves a dictation into an unedited transcript and leaving asks nothing", async () => {
			const w = await mountSuspended(RecordingPage);
			await dictate(w);
			expect(area(w).value).toBe("Hallo Welt diktiert ");
			expect(store.updateTranscript).toHaveBeenCalledTimes(1);
			expect(store.updateTranscript).toHaveBeenCalledWith("t-1", { text: "Hallo Welt diktiert " });
			// New content, not a correction of the engine's text.
			expect(store.recordCorrection).not.toHaveBeenCalled();
			expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Gespeichert");
			expect(w.find("[data-testid=\"save-transcript\"]").attributes("disabled")).toBeDefined();
			expect(guards.leave!(elsewhere, here)).toBe(true);
		});

		it("saves only the dictated part next to unsaved typing, and leaving still asks", async () => {
			const w = await mountSuspended(RecordingPage);
			await w.find("[data-testid=\"transcript-edit\"]").setValue("Hallo Welt, geändert");
			await dictate(w);
			expect(area(w).value).toBe("Hallo Welt, geändert diktiert ");
			// The typing is not written along.
			expect(store.updateTranscript).toHaveBeenCalledTimes(1);
			expect(store.updateTranscript).toHaveBeenCalledWith("t-1", { text: "Hallo Welt diktiert " });
			expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Ungespeicherte Änderungen");

			const result = guards.leave!(elsewhere, here);
			await flushPromises();
			expect(w.find("[data-testid=\"leave-dialog\"]").exists()).toBe(true);
			await w.find("[data-testid=\"leave-save\"]").trigger("click");
			expect(await result).toBe(true);
			expect(store.updateTranscript).toHaveBeenLastCalledWith("t-1", { text: "Hallo Welt, geändert diktiert " });
		});

		it("shows a chip while dictating that changes neither the text nor the save state", async () => {
			const reply = Promise.withResolvers<Response>();
			globalThis.fetch = vi.fn(() => reply.promise) as unknown as typeof fetch;
			const w = await mountSuspended(RecordingPage);
			const ptt = w.find("[data-testid=\"push-to-talk\"]");
			await ptt.trigger("pointerdown", { pointerId: 1 });
			await flushPromises();
			expect(area(w).dataset.dictationParts).toMatch(/^ptt-\d+:recording$/);
			expect(area(w).value).toBe("Hallo Welt");
			heldFor(500);
			await ptt.trigger("pointerup", { pointerId: 1 });
			await flushPromises();
			expect(area(w).dataset.dictationParts).toMatch(/^ptt-\d+:transcribing$/);
			expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Gespeichert");
			expect(w.find("[data-testid=\"save-transcript\"]").attributes("disabled")).toBeDefined();

			reply.resolve(new Response(JSON.stringify({ text: "diktiert" }), { status: 200, headers: { "Content-Type": "application/json" } }));
			await vi.waitFor(() => expect(area(w).value).toBe("Hallo Welt diktiert "));
			expect(area(w).dataset.dictationParts).toBe("");
		});

		it("puts the dictation where its chip stands after typing before it, and saves only the dictation", async () => {
			const reply = Promise.withResolvers<Response>();
			globalThis.fetch = vi.fn(() => reply.promise) as unknown as typeof fetch;
			// Caret after "Hallo": the chip goes there, not to the end.
			editorCaret.value = 5;
			const w = await mountSuspended(RecordingPage);
			const ptt = w.find("[data-testid=\"push-to-talk\"]");
			await ptt.trigger("pointerdown", { pointerId: 1 });
			await flushPromises();
			heldFor(500);
			await ptt.trigger("pointerup", { pointerId: 1 });
			await flushPromises();
			// The user keeps typing in front of the chip while the dictation transcribes.
			await w.find("[data-testid=\"transcript-edit\"]").setValue("Oh, Hallo Welt");

			reply.resolve(new Response(JSON.stringify({ text: "diktiert" }), { status: 200, headers: { "Content-Type": "application/json" } }));
			await vi.waitFor(() => expect(area(w).value).toBe("Oh, Hallo diktiert Welt"));
			await flushPromises();
			// Only the dictation is written, at its place in the saved text; the typing stays unsaved.
			expect(store.updateTranscript).toHaveBeenCalledTimes(1);
			expect(store.updateTranscript).toHaveBeenCalledWith("t-1", { text: "Hallo diktiert Welt" });
			expect(store.recordCorrection).not.toHaveBeenCalled();
			expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Ungespeicherte Änderungen");
		});

		it("a dictation whose save failed stays unsaved, shows the error and leaving asks", async () => {
			store.updateTranscript.mockRejectedValueOnce(new Error("offline"));
			const w = await mountSuspended(RecordingPage);
			await dictate(w);
			expect(area(w).value).toBe("Hallo Welt diktiert ");
			expect(w.find("[data-testid=\"save-error\"]").exists()).toBe(true);
			expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Ungespeicherte Änderungen");
			expect(w.find("[data-testid=\"save-transcript\"]").attributes("disabled")).toBeUndefined();

			const result = guards.leave!(elsewhere, here);
			await flushPromises();
			expect(w.find("[data-testid=\"leave-dialog\"]").exists()).toBe(true);
			await w.find("[data-testid=\"leave-save\"]").trigger("click");
			expect(await result).toBe(true);
			expect(store.updateTranscript).toHaveBeenLastCalledWith("t-1", { text: "Hallo Welt diktiert " });
		});

		it("asks before leaving while a dictation is still transcribing", async () => {
			const confirm = vi.fn(() => false);
			vi.stubGlobal("confirm", confirm);
			globalThis.fetch = vi.fn(() => new Promise<Response>(() => {})) as unknown as typeof fetch;
			const w = await mountSuspended(RecordingPage);
			const ptt = w.find("[data-testid=\"push-to-talk\"]");
			await ptt.trigger("pointerdown", { pointerId: 1 });
			await flushPromises();
			heldFor(500);
			await ptt.trigger("pointerup", { pointerId: 1 });
			await flushPromises();
			expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Gespeichert");
			expect(guards.leave!(elsewhere, here)).toBe(false);
			expect(confirm).toHaveBeenCalledWith("Das laufende Diktat geht verloren. Trotzdem verlassen?");
			vi.unstubAllGlobals();
		});
	});

	it("shows the back button and no state chip for a transcribed recording on phones", async () => {
		const w = await mountSuspended(RecordingPage);
		expect(w.find("[data-testid=\"back\"]").exists()).toBe(true);
		expect(w.text()).not.toContain("Transkribiert…");
	});

	it("push-to-talk: shows a chip on press, records, transcribes via REST, replaces the chip", async () => {
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
		// Chip inserted into the editor, the text itself unchanged.
		const areaAfter = w.find("[data-testid=\"transcript-edit\"]").element as HTMLTextAreaElement;
		expect(areaAfter.dataset.dictationParts).toMatch(/^ptt-\d+:recording$/);
		expect(areaAfter.value).toBe("Hallo Welt");

		heldFor(500);
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
		// Chip replaced with the transcribed text
		await vi.waitFor(() => {
			const area = w.find("[data-testid=\"transcript-edit\"]").element as HTMLTextAreaElement;
			expect(area.value).toContain("diktierter satz");
		});
		const areaFinal = w.find("[data-testid=\"transcript-edit\"]").element as HTMLTextAreaElement;
		expect(areaFinal.value).toContain("Hallo Welt");
		expect(areaFinal.dataset.dictationParts).toBe("");
	});

	/** Makes the next pointerup look like it came after a long press (hold = push-to-talk, tap = latch). */
	afterEach(() => {
		vi.restoreAllMocks();
	});

	function heldFor(ms: number): void {
		const now = Date.now();
		vi.spyOn(Date, "now").mockReturnValue(now + ms);
	}

	it("leaving with unsaved text edits asks, and Verwerfen restores the saved text", async () => {
		const w = await mountSuspended(RecordingPage);
		const area = w.find("[data-testid=\"transcript-edit\"]");
		const saved = (area.element as HTMLTextAreaElement).value;
		await area.setValue(`${saved} geändert`);
		const here = { params: { id: "rec-1" } };
		const result = guards.leave!({ params: { id: "rec-2" } }, here);
		await w.vm.$nextTick();
		expect(w.find("[data-testid=\"leave-dialog\"]").exists()).toBe(true);
		await w.find("[data-testid=\"leave-discard\"]").trigger("click");
		expect(await result).toBe(true);
		expect((w.find("[data-testid=\"transcript-edit\"]").element as HTMLTextAreaElement).value).toBe(saved);
	});

	it("tapping the dictation button latches recording until the next tap", async () => {
		globalThis.fetch = vi.fn(async () => new Response(
			JSON.stringify({ text: "satz" }),
			{ status: 200, headers: { "Content-Type": "application/json" } },
		)) as unknown as typeof fetch;
		const w = await mountSuspended(RecordingPage);
		const ptt = w.find("[data-testid=\"push-to-talk\"]");
		await ptt.trigger("pointerdown", { pointerId: 1 });
		await w.vm.$nextTick();
		await ptt.trigger("pointerup", { pointerId: 1 });
		await w.vm.$nextTick();
		expect(globalThis.fetch).not.toHaveBeenCalled();
		expect(ptt.text()).toContain("tippen zum Beenden");
		await ptt.trigger("pointerdown", { pointerId: 1 });
		await ptt.trigger("pointerup", { pointerId: 1 });
		await new Promise(r => setTimeout(r, 0));
		await w.vm.$nextTick();
		expect(globalThis.fetch).toHaveBeenCalled();
	});

	it("push-to-talk without a hub token records the diagnostic and never calls the hub", async () => {
		useHubSettings().token = "";
		globalThis.fetch = vi.fn() as unknown as typeof fetch;
		const w = await mountSuspended(RecordingPage);
		const ptt = w.find("[data-testid=\"push-to-talk\"]");
		await ptt.trigger("pointerdown", { pointerId: 1 });
		await w.vm.$nextTick();
		heldFor(500);
		await ptt.trigger("pointerup", { pointerId: 1 });
		await w.vm.$nextTick();
		await new Promise(r => setTimeout(r, 0));
		await w.vm.$nextTick();
		expect(globalThis.fetch).not.toHaveBeenCalled();
		const fail = usePttDiagnostics().entries.value.find(e => !e.ok);
		expect(fail?.detail).toBe("Kein Hub-Token – in Einstellungen → Verbindung eintragen");
	});

	describe("Nachbearbeiten", () => {
		const PROCESSED = "## Befund\n\nHallo Welt";
		const area = (w: VueWrapper) => w.find("[data-testid=\"transcript-edit\"]").element as HTMLTextAreaElement;

		beforeEach(() => {
			useHubSettings().token = "tok";
		});

		function processedTranscript(over: Partial<Transcript> = {}): Transcript {
			return {
				id: "t-1", recording: "rec-1", text: PROCESSED, raw_text: "Hallo Welt",
				applied_profile: "transcript-markdown-de", processed_text: PROCESSED, postprocess_status: "done", ...over,
			};
		}

		it("requests a run on the saved text, locks the text while it runs and shows the result rendered", async () => {
			const w = await mountSuspended(RecordingPage);
			await flushPromises();
			// The panel writes the request; the page refreshes and keeps polling.
			refresh.mockImplementationOnce(async () => {
				live.transcript!.value = { ...live.transcript!.value!, postprocess_status: "pending", postprocess_profile: "transcript-markdown-de" };
			});
			await w.find("[data-testid=\"postprocess-run\"]").trigger("click");
			await flushPromises();
			expect(store.updateTranscript).toHaveBeenCalledWith("t-1", {
				postprocess_status: "pending", postprocess_profile: "transcript-markdown-de",
			});
			expect(startPolling).toHaveBeenCalledTimes(2);
			expect(area(w).readOnly).toBe(true);
			expect(w.find("[data-testid=\"push-to-talk\"]").exists()).toBe(false);

			// A poll delivers the profile's output: shown rendered, the raw text stays.
			live.transcript!.value = { ...live.transcript!.value!, ...processedTranscript() };
			await flushPromises();
			const rendered = w.find("[data-testid=\"transcript-rendered\"]");
			expect(rendered.find("h3").text()).toBe("Befund");
			expect(rendered.find("p").text()).toBe("Hallo Welt");
			expect(w.find("[data-testid=\"transcript-edit\"]").exists()).toBe(false);
			expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Gespeichert");
			expect(w.find("[data-testid=\"postprocess-raw\"] pre").text()).toBe("Hallo Welt");

			// "Bearbeiten" opens the Markdown source in the editor.
			await w.find("[data-testid=\"transcript-view-toggle\"]").trigger("click");
			expect(area(w).readOnly).toBe(false);
			expect(area(w).value).toBe(PROCESSED);
		});

		it("is blocked while the text has unsaved edits", async () => {
			const w = await mountSuspended(RecordingPage);
			await w.find("[data-testid=\"transcript-edit\"]").setValue("Hallo Welt, geändert");
			expect(w.find("[data-testid=\"postprocess-run\"]").attributes("disabled")).toBeDefined();
			expect(w.find("[data-testid=\"postprocess-blocked\"]").exists()).toBe(true);
		});

		it("does not store edits of a profile's output as engine corrections", async () => {
			transcript.value = processedTranscript();
			const w = await mountSuspended(RecordingPage);
			await w.find("[data-testid=\"transcript-view-toggle\"]").trigger("click");
			await w.find("[data-testid=\"transcript-edit\"]").setValue("## Befund gekürzt");
			await w.find("[data-testid=\"save-transcript\"]").trigger("click");
			await flushPromises();
			expect(store.updateTranscript).toHaveBeenCalledWith("t-1", { text: "## Befund gekürzt" });
			expect(store.recordCorrection).not.toHaveBeenCalled();
		});

		it("appends a re-dictated part to the rendered profile output and to the raw transcript", async () => {
			transcript.value = processedTranscript();
			const w = await mountSuspended(RecordingPage);
			// The rendered view has no editor, so there is no chip: the text is appended.
			await landPart(await recordPart(w), "Neuer Teil");
			expect(store.updateTranscript).toHaveBeenCalledWith("t-1", {
				text: `${PROCESSED}\nNeuer Teil`,
				raw_text: "Hallo Welt\nNeuer Teil",
				processed_text: `${PROCESSED}\nNeuer Teil`,
			});
			expect(w.find("[data-testid=\"postprocess-raw\"] pre").text()).toBe("Hallo Welt\nNeuer Teil");
		});

		it("dictation into the rendered view opens the editor and saves the part with the raw transcript", async () => {
			globalThis.fetch = vi.fn(async () => new Response(
				JSON.stringify({ text: "diktiert" }),
				{ status: 200, headers: { "Content-Type": "application/json" } },
			)) as unknown as typeof fetch;
			transcript.value = processedTranscript();
			const w = await mountSuspended(RecordingPage);
			const ptt = w.find("[data-testid=\"push-to-talk\"]");
			await ptt.trigger("pointerdown", { pointerId: 1 });
			await flushPromises();
			expect(area(w).dataset.dictationParts).toMatch(/^ptt-\d+:recording$/);
			expect(area(w).value).toBe(PROCESSED);
			heldFor(500);
			await ptt.trigger("pointerup", { pointerId: 1 });
			await vi.waitFor(() => expect(area(w).value).toBe(`${PROCESSED} diktiert `));
			// Saved on its own, no "Speichern" needed.
			await vi.waitFor(() => expect(store.updateTranscript).toHaveBeenCalledWith("t-1", {
				text: `${PROCESSED} diktiert `,
				raw_text: "Hallo Welt\ndiktiert",
				processed_text: `${PROCESSED} diktiert `,
			}));
			await flushPromises();
			expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Gespeichert");
		});

		describe("asks before a re-run or restore drops saved edits", () => {
			let confirm: ReturnType<typeof vi.fn>;

			beforeEach(() => {
				confirm = vi.fn(() => false);
				vi.stubGlobal("confirm", confirm);
			});

			afterEach(() => {
				vi.unstubAllGlobals();
			});

			async function saveTypedEdit(w: VueWrapper): Promise<void> {
				await w.find("[data-testid=\"transcript-view-toggle\"]").trigger("click");
				await w.find("[data-testid=\"transcript-edit\"]").setValue("## Befund gekürzt");
				await w.find("[data-testid=\"save-transcript\"]").trigger("click");
				await flushPromises();
			}

			it("asks before re-running after a saved edit", async () => {
				transcript.value = processedTranscript();
				const w = await mountSuspended(RecordingPage);
				await saveTypedEdit(w);
				store.updateTranscript.mockClear();
				await w.find("[data-testid=\"postprocess-run\"]").trigger("click");
				await flushPromises();
				expect(confirm).toHaveBeenCalledTimes(1);
				expect(store.updateTranscript).not.toHaveBeenCalled();
			});

			it("asks before restoring the raw transcript after a saved edit", async () => {
				transcript.value = processedTranscript();
				const w = await mountSuspended(RecordingPage);
				await saveTypedEdit(w);
				store.updateTranscript.mockClear();
				await w.find("[data-testid=\"postprocess-restore\"]").trigger("click");
				await flushPromises();
				expect(confirm).toHaveBeenCalledTimes(1);
				expect(store.updateTranscript).not.toHaveBeenCalled();
			});

			it("does not ask when only a continued part was added", async () => {
				transcript.value = processedTranscript();
				const w = await mountSuspended(RecordingPage);
				await landPart(await recordPart(w), "Neuer Teil");
				store.updateTranscript.mockClear();
				await w.find("[data-testid=\"postprocess-run\"]").trigger("click");
				await flushPromises();
				expect(confirm).not.toHaveBeenCalled();
				expect(store.updateTranscript).toHaveBeenCalledWith("t-1", {
					postprocess_status: "pending", postprocess_profile: "transcript-markdown-de",
				});
			});

			it("saves a continued part on its own and still asks after the typed edits are saved", async () => {
				transcript.value = processedTranscript();
				const w = await mountSuspended(RecordingPage);
				await w.find("[data-testid=\"transcript-view-toggle\"]").trigger("click");
				await w.find("[data-testid=\"transcript-edit\"]").setValue("## Befund gekürzt");
				await landPart(await recordPart(w), "Neuer Teil");
				// The part goes onto the saved profile output, the typing stays unsaved.
				expect(store.updateTranscript).toHaveBeenCalledWith("t-1", {
					text: `${PROCESSED}\nNeuer Teil`,
					raw_text: "Hallo Welt\nNeuer Teil",
					processed_text: `${PROCESSED}\nNeuer Teil`,
				});
				expect(area(w).value).toBe("## Befund gekürzt\nNeuer Teil");
				expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Ungespeicherte Änderungen");

				await w.find("[data-testid=\"save-transcript\"]").trigger("click");
				await flushPromises();
				expect(store.updateTranscript).toHaveBeenLastCalledWith("t-1", { text: "## Befund gekürzt\nNeuer Teil" });
				store.updateTranscript.mockClear();
				await w.find("[data-testid=\"postprocess-run\"]").trigger("click");
				await flushPromises();
				expect(confirm).toHaveBeenCalledTimes(1);
				expect(store.updateTranscript).not.toHaveBeenCalled();
			});
		});
	});

	describe("re-dictation (Weiter aufnehmen)", () => {
		const area = (w: VueWrapper) => w.find("[data-testid=\"transcript-edit\"]").element as HTMLTextAreaElement;
		const chips = (w: VueWrapper) => area(w).dataset.dictationParts;

		it("records in the record bar in place of the player, with a chip at the end", async () => {
			const w = await mountSuspended(RecordingPage);
			await vi.waitFor(() => expect(w.findComponent({ name: "HPlayerDock" }).exists()).toBe(true));
			await w.find("[data-testid=\"continue-recording\"]").trigger("click");
			await flushPromises();
			expect(redict.start).toHaveBeenCalledWith("part-1");
			expect(w.find("[data-testid=\"record-bar\"]").exists()).toBe(true);
			expect(w.findComponent({ name: "HPlayerDock" }).exists()).toBe(false);
			expect(chips(w)).toBe("part-1:recording");
			// The chip adds nothing to the text, so nothing is unsaved.
			expect(area(w).value).toBe("Hallo Welt");
			expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Gespeichert");

			await w.find("[data-testid=\"stop\"]").trigger("click");
			await flushPromises();
			expect(redict.finish).toHaveBeenCalledOnce();
			expect(chips(w)).toBe("part-1:transcribing");
			expect(w.find("[data-testid=\"record-bar\"]").exists()).toBe(false);
			expect(w.findComponent({ name: "HPlayerDock" }).exists()).toBe(true);
		});

		it("lands the text as its own paragraph and saves only it, unsaved typing stays unsaved", async () => {
			const w = await mountSuspended(RecordingPage);
			await w.find("[data-testid=\"transcript-edit\"]").setValue("Hallo Welt, geändert");
			await landPart(await recordPart(w), "Neuer Teil");

			expect(area(w).value).toBe("Hallo Welt, geändert\nNeuer Teil");
			expect(chips(w)).toBe("");
			expect(store.updateTranscript).toHaveBeenCalledTimes(1);
			expect(store.updateTranscript).toHaveBeenCalledWith("t-1", { text: "Hallo Welt\nNeuer Teil" });
			expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Ungespeicherte Änderungen");

			// The typing still needs "Speichern": leaving asks.
			const result = guards.leave!({ params: { id: "rec-2" } }, { params: { id: "rec-1" } });
			await flushPromises();
			expect(w.find("[data-testid=\"leave-dialog\"]").exists()).toBe(true);
			await w.find("[data-testid=\"leave-save\"]").trigger("click");
			expect(await result).toBe(true);
			expect(store.updateTranscript).toHaveBeenLastCalledWith("t-1", { text: "Hallo Welt, geändert\nNeuer Teil" });
		});

		it("a part on an unedited transcript leaves nothing to save", async () => {
			const w = await mountSuspended(RecordingPage);
			const part = await recordPart(w);
			const reloads = listReloads();
			await landPart(part, "Neuer Teil");
			expect(store.updateTranscript).toHaveBeenCalledWith("t-1", { text: "Hallo Welt\nNeuer Teil" });
			expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Gespeichert");
			expect(listReloads()).toBe(reloads + 1);
			expect(guards.leave!({ params: { id: "rec-2" } }, { params: { id: "rec-1" } })).toBe(true);
		});

		it("adds no separator when the text already ends with a newline", async () => {
			transcript.value = { id: "t-1", recording: "rec-1", text: "Erster Absatz\n" };
			const w = await mountSuspended(RecordingPage);
			await landPart(await recordPart(w), "Neuer Teil");
			expect(store.updateTranscript).toHaveBeenCalledWith("t-1", { text: "Erster Absatz\nNeuer Teil" });
		});

		it("while editing, the part splits the paragraph at the caret", async () => {
			// Caret after "Hallo".
			editorCaret.value = 5;
			const w = await mountSuspended(RecordingPage);
			await landPart(await recordPart(w), "neu");
			expect(area(w).value).toBe("Hallo\nneu\n Welt");
			expect(store.updateTranscript).toHaveBeenCalledWith("t-1", { text: "Hallo\nneu\n Welt" });
			expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Gespeichert");
		});

		it("records the next part while the first still transcribes; each lands at its own chip", async () => {
			const w = await mountSuspended(RecordingPage);
			const first = await recordPart(w);
			expect(w.find("[data-testid=\"continue-recording\"]").attributes("disabled")).toBeUndefined();
			const second = await recordPart(w);
			expect(chips(w)).toBe(`${first}:transcribing ${second}:transcribing`);

			// The second job finishes first; the order in the text still follows the chips.
			await landPart(second, "Zwei");
			await landPart(first, "Eins");
			expect(area(w).value).toBe("Hallo Welt\nEins\nZwei");
			expect(store.updateTranscript).toHaveBeenNthCalledWith(1, "t-1", { text: "Hallo Welt\nZwei" });
			expect(store.updateTranscript).toHaveBeenNthCalledWith(2, "t-1", { text: "Hallo Welt\nEins\nZwei" });
			expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Gespeichert");
		});

		it("typing and Speichern stay free while a part transcribes", async () => {
			const w = await mountSuspended(RecordingPage);
			const part = await recordPart(w);
			expect(area(w).readOnly).toBe(false);
			await w.find("[data-testid=\"transcript-edit\"]").setValue("Hallo Welt!");
			const save = w.find("[data-testid=\"save-transcript\"]");
			expect(save.attributes("disabled")).toBeUndefined();
			await save.trigger("click");
			await flushPromises();
			expect(store.updateTranscript).toHaveBeenLastCalledWith("t-1", { text: "Hallo Welt!" });

			await landPart(part, "Teil");
			expect(area(w).value).toBe("Hallo Welt!\nTeil");
			expect(store.updateTranscript).toHaveBeenLastCalledWith("t-1", { text: "Hallo Welt!\nTeil" });
		});

		it("a failed part shows retry and discard; retry transcribes again and the text lands", async () => {
			const w = await mountSuspended(RecordingPage);
			const part = await recordPart(w);
			await failPart(part, "Transkription fehlgeschlagen: Hub weg");
			expect(chips(w)).toBe(`${part}:error`);
			expect(w.find("[data-testid=\"continue-failed\"]").text()).toContain("Transkription fehlgeschlagen: Hub weg");

			await w.find("[data-testid=\"continue-retry\"]").trigger("click");
			await nextTick();
			expect(redict.retry).toHaveBeenCalledWith(part);
			expect(chips(w)).toBe(`${part}:transcribing`);
			expect(w.find("[data-testid=\"continue-failed\"]").exists()).toBe(false);

			await landPart(part, "Nachgereicht");
			expect(store.updateTranscript).toHaveBeenCalledWith("t-1", { text: "Hallo Welt\nNachgereicht" });
		});

		it("discarding a failed part drops it and its chip", async () => {
			const w = await mountSuspended(RecordingPage);
			const part = await recordPart(w);
			await failPart(part, "Kein Hub-Token");
			await w.find("[data-testid=\"continue-discard\"]").trigger("click");
			await nextTick();
			expect(redict.discard).toHaveBeenCalledWith(part);
			expect(chips(w)).toBe("");
			expect(w.find("[data-testid=\"continue-failed\"]").exists()).toBe(false);
			expect(store.updateTranscript).not.toHaveBeenCalled();
		});

		it("cancel in the record bar drops the part and its chip", async () => {
			const w = await mountSuspended(RecordingPage);
			const reloads = listReloads();
			await w.find("[data-testid=\"continue-recording\"]").trigger("click");
			await flushPromises();
			await w.find("[data-testid=\"cancel\"]").trigger("click");
			await flushPromises();
			expect(redict.cancel).toHaveBeenCalledOnce();
			expect(redict.finish).not.toHaveBeenCalled();
			expect(chips(w)).toBe("");
			expect(w.find("[data-testid=\"record-bar\"]").exists()).toBe(false);
			expect(listReloads()).toBe(reloads);
		});

		it("an empty part removes its chip", async () => {
			redict.finish.mockResolvedValueOnce(false);
			const w = await mountSuspended(RecordingPage);
			await w.find("[data-testid=\"continue-recording\"]").trigger("click");
			await flushPromises();
			await w.find("[data-testid=\"stop\"]").trigger("click");
			await flushPromises();
			expect(chips(w)).toBe("");
		});

		it("no recognised text removes the chip, saves nothing and says so", async () => {
			const w = await mountSuspended(RecordingPage);
			await landPart(await recordPart(w), "");
			expect(chips(w)).toBe("");
			expect(area(w).value).toBe("Hallo Welt");
			expect(store.updateTranscript).not.toHaveBeenCalled();
			expect(w.find("[data-testid=\"continue-notice\"]").text()).toBe("Im neuen Teil wurde kein Text erkannt.");
		});

		it("shows a microphone failure inline, removes the chip and allows another try", async () => {
			redict.start.mockImplementationOnce(async () => {
				redict.startError!.value = "Mikrofon nicht verfügbar";
				return false;
			});
			const w = await mountSuspended(RecordingPage);
			await w.find("[data-testid=\"continue-recording\"]").trigger("click");
			await flushPromises();
			expect(w.find("[data-testid=\"continue-error\"]").text()).toBe("Mikrofon nicht verfügbar");
			expect(chips(w)).toBe("");
			expect(w.find("[data-testid=\"record-bar\"]").exists()).toBe(false);
			expect(w.find("[data-testid=\"continue-recording\"]").attributes("disabled")).toBeUndefined();
		});

		it("a landed part whose save failed stays unsaved and says so", async () => {
			store.updateTranscript.mockRejectedValueOnce(new Error("offline"));
			const w = await mountSuspended(RecordingPage);
			await w.find("[data-testid=\"transcript-edit\"]").setValue("Hallo Welt, geändert");
			await landPart(await recordPart(w), "Neuer Teil");
			expect(store.updateTranscript).toHaveBeenCalledTimes(1);
			expect(area(w).value).toBe("Hallo Welt, geändert\nNeuer Teil");
			expect(w.find("[data-testid=\"save-state\"]").text()).toBe("Ungespeicherte Änderungen");
			expect(w.find("[data-testid=\"save-transcript\"]").attributes("disabled")).toBeUndefined();
			expect(w.find("[data-testid=\"continue-notice\"]").text()).toBe("Text eingefügt, aber nicht gespeichert – bitte Speichern.");
		});

		it("creates the transcript row with just the new text when there was none", async () => {
			transcript.value = null;
			store.createTranscript.mockResolvedValueOnce({ id: "t-new", recording: "rec-1", text: "Neuer Teil" });
			const w = await mountSuspended(RecordingPage);
			await landPart(await recordPart(w), "Neuer Teil");
			expect(store.createTranscript).toHaveBeenCalledOnce();
			expect(store.createTranscript).toHaveBeenCalledWith("rec-1", { text: "Neuer Teil" });
		});

		it("switches the player to the appended audio and reloads the list", async () => {
			const w = await mountSuspended(RecordingPage);
			await vi.waitFor(() => expect(w.findComponent({ name: "HPlayerDock" }).exists()).toBe(true));
			const part = await recordPart(w);
			const reloads = listReloads();
			redict.hooks!.onAudio(mergedAudio(part));
			await vi.waitFor(() =>
				expect(w.findComponent({ name: "HPlayerDock" }).props("src")).toBe("http://directus/assets/file-2"),
			);
			expect(w.findComponent({ name: "HPlayerDock" }).props("fallbackDuration")).toBe(70);
			expect(listReloads()).toBe(reloads + 1);
		});

		it("keeps the old audio and shows a notice when appending the audio failed", async () => {
			const w = await mountSuspended(RecordingPage);
			await vi.waitFor(() => expect(w.findComponent({ name: "HPlayerDock" }).exists()).toBe(true));
			const part = await recordPart(w);
			const src = w.findComponent({ name: "HPlayerDock" }).props("src");
			await landPart(part, "Neuer Teil");
			redict.hooks!.onAudio({ partId: part, ok: false, error: "Decoder kaputt" });
			await flushPromises();
			expect(w.find("[data-testid=\"continue-notice\"]").text()).toBe("Audio konnte nicht angehängt werden: Decoder kaputt");
			expect(w.findComponent({ name: "HPlayerDock" }).props("src")).toBe(src);
		});

		it("a part whose audio was not saved offers to append it again and guards leaving", async () => {
			const confirm = vi.fn((_message?: string) => false);
			vi.stubGlobal("confirm", confirm);
			const w = await mountSuspended(RecordingPage);
			const part = await recordPart(w);
			await landPart(part, "Neuer Teil");
			// The engine keeps the part (and its blob) while its audio is not saved.
			redict.parts!.value = [{ id: part, status: "done", error: null, audio: "failed", audioError: "Decoder kaputt" }];
			await nextTick();
			const banner = w.find("[data-testid=\"continue-audio-failed\"]");
			expect(banner.text()).toContain("Audio nicht gespeichert – Erneut anhängen");
			expect(banner.text()).toContain("Decoder kaputt");

			const here = { params: { id: "rec-1" } };
			expect(guards.leave!({ params: { id: "rec-2" } }, here)).toBe(false);
			expect(confirm).toHaveBeenCalledWith("Ein Nachdiktat ist fehlgeschlagen und geht beim Verlassen verloren. Trotzdem verlassen?");

			await w.find("[data-testid=\"continue-audio-retry\"]").trigger("click");
			await nextTick();
			expect(redict.retryAudio).toHaveBeenCalledWith(part);
			expect(w.find("[data-testid=\"continue-audio-failed\"]").exists()).toBe(false);
			vi.unstubAllGlobals();
		});

		it("asks before leaving when a part's transcription failed", async () => {
			const confirm = vi.fn((_message?: string) => false);
			vi.stubGlobal("confirm", confirm);
			const w = await mountSuspended(RecordingPage);
			await failPart(await recordPart(w), "Hub weg");
			expect(guards.leave!({ params: { id: "rec-2" } }, { params: { id: "rec-1" } })).toBe(false);
			expect(confirm).toHaveBeenCalledWith("Ein Nachdiktat ist fehlgeschlagen und geht beim Verlassen verloren. Trotzdem verlassen?");
			vi.unstubAllGlobals();
		});

		it("keeps the progress in one polite live region", async () => {
			const w = await mountSuspended(RecordingPage);
			const live = () => w.find("[data-testid=\"continue-live\"]");
			expect(live().attributes("role")).toBe("status");
			expect(live().attributes("aria-live")).toBe("polite");
			expect(live().text()).toBe("");
			const region = live().element;
			await recordPart(w);
			expect(live().element).toBe(region);
			expect(live().text()).toBe("1 Teil wird transkribiert – Seite bitte geöffnet lassen.");
			await recordPart(w);
			expect(live().text()).toBe("2 Teile werden transkribiert – Seite bitte geöffnet lassen.");

			redict.parts!.value = [];
			redict.merging!.value = true;
			await nextTick();
			expect(live().text()).toBe("Audio wird angehängt…");
		});

		it("left Ctrl does not start dictation while a part is recording, but does while one transcribes", async () => {
			// Pages mounted by earlier specs still listen on window, so judge this
			// page by its own editor: dictation starts by inserting a dictation chip.
			const w = await mountSuspended(RecordingPage);
			const ctrl = async () => {
				window.dispatchEvent(new KeyboardEvent("keydown", { code: "ControlLeft" }));
				await flushPromises();
			};
			await w.find("[data-testid=\"continue-recording\"]").trigger("click");
			await flushPromises();
			await ctrl();
			expect(chips(w)).toBe("part-1:recording");
			expect(w.find("[data-testid=\"push-to-talk\"]").attributes("disabled")).toBeDefined();

			await w.find("[data-testid=\"stop\"]").trigger("click");
			await flushPromises();
			await ctrl();
			expect(chips(w)).toMatch(/^part-1:transcribing ptt-\d+:recording$/);
			w.unmount();
		});

		it("disables Neu transkribieren while a part is in flight or failed", async () => {
			recording.value = baseRecording({ status: "error", error_message: "Boom" });
			transcript.value = null;
			const w = await mountSuspended(RecordingPage);
			const restart = () => w.find("[data-testid=\"restart-transcription\"]");
			expect(restart().attributes("disabled")).toBeUndefined();
			const part = await recordPart(w);
			expect(restart().attributes("disabled")).toBeDefined();
			await failPart(part, "Hub weg");
			expect(restart().attributes("disabled")).toBeDefined();
		});

		it("asks before leaving while a part would be lost", async () => {
			const confirm = vi.fn((_message?: string) => true);
			vi.stubGlobal("confirm", confirm);
			const w = await mountSuspended(RecordingPage);
			const here = { params: { id: "rec-1" } };
			const other = { params: { id: "rec-2" } };

			expect(guards.leave!(other, here)).toBe(true);
			expect(confirm).not.toHaveBeenCalled();

			await recordPart(w);
			confirm.mockReturnValueOnce(false);
			expect(guards.leave!(other, here)).toBe(false);
			expect(confirm).toHaveBeenCalledWith("Das laufende Nachdiktat geht verloren. Trotzdem verlassen?");
			confirm.mockReturnValueOnce(true);
			expect(guards.leave!(other, here)).toBe(true);

			// Switching to another recording in the desktop list reuses the route record.
			confirm.mockReturnValueOnce(false);
			expect(guards.update!(other, here)).toBe(false);
			expect(guards.update!(here, here)).toBe(true);
			expect(confirm).toHaveBeenCalledTimes(3);
			vi.unstubAllGlobals();
		});

		it("is not offered while the recording is still transcribing", async () => {
			recording.value = baseRecording({ status: "pending" });
			transcript.value = null;
			const w = await mountSuspended(RecordingPage);
			expect(w.find("[data-testid=\"continue-recording\"]").exists()).toBe(false);
		});
	});
});
