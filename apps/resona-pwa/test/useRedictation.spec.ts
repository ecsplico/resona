import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { effectScope, type EffectScope } from "vue";
import { useRedictation, type Redictation, type RedictationAudio, type RedictationHooks } from "~/composables/useRedictation";
import type { NewRecordingPart, RecordingPartPatch } from "~/data/types";
import type { Recording, RecordingPart } from "~/types/directus";

// Stored parts: the processor (worker / localProcessor) is played by the tests,
// which move rows along with `processed`.
const rows = vi.hoisted(() => new Map<string, RecordingPart>());

const store = vi.hoisted(() => ({
	audioUrl: vi.fn(async (_rec: Recording): Promise<string | null> => "http://d/assets/f-old"),
	replaceAudio: vi.fn(async (_id: string, _audio: Blob, _name: string, seconds: number) => ({ audio_file: `f-${seconds}`, duration_seconds: seconds })),
	listParts: vi.fn(async (recordingId: string) =>
		[...rows.values()].filter(r => r.recording === recordingId).sort((a, b) => a.seq - b.seq).map(r => ({ ...r }))),
	createPart: vi.fn(async (input: NewRecordingPart, audio: Blob, _name: string) => {
		const row: RecordingPart = { ...input, status: "pending", audio: `file-${input.id}`, offset_seconds: null, duration_seconds: null, text: null };
		rows.set(row.id, row);
		blobs.set(row.audio!, audio);
		return { ...row };
	}),
	updatePart: vi.fn(async (id: string, patch: RecordingPartPatch) => {
		const row = rows.get(id);
		if (!row) throw new Error("Diktatteil nicht gefunden");
		rows.set(id, { ...row, ...patch });
	}),
	partAudio: vi.fn(async (part: RecordingPart) => (part.audio ? blobs.get(part.audio) ?? null : null)),
}));
const blobs = vi.hoisted(() => new Map<string, Blob>());

const recorder = vi.hoisted(() => ({
	start: vi.fn(async () => true),
	stop: vi.fn(async (): Promise<Blob> => new Blob(["part"], { type: "audio/webm" })),
	elapsed: { value: 0 },
	levels: { value: [] as number[] },
	paused: { value: false },
	sessionId: { value: null as string | null },
	pause: vi.fn(),
	resume: vi.fn(),
}));

const unfinished = vi.hoisted(() => ({ discardUnfinished: vi.fn(async (_id: string) => {}) }));
vi.mock("~/data/unfinishedRecordings", () => unfinished);

const merge = vi.hoisted(() => ({
	// Every part adds 6 s to the merged audio.
	mergeToWav: vi.fn(async (parts: Blob[]) => ({
		blob: new Blob(["wav"], { type: "audio/wav" }),
		seconds: parts.length === 2 ? merge.total + 6 : 6,
	})),
	total: 10,
}));

mockNuxtImport("useDataStore", () => () => store);
mockNuxtImport("useRecorder", () => () => recorder);
vi.mock("~/utils/audioMerge", () => ({ mergeToWav: merge.mergeToWav }));

const fetchMock = vi.fn(async (_url: string) => new Response("old-audio", { headers: { "Content-Type": "audio/webm" } }));
const realFetch = globalThis.fetch;

let scope: EffectScope;
let current: Recording;
const texts: [string, string][] = [];
const errors: [string, string][] = [];
const audio: RedictationAudio[] = [];
const restored: string[] = [];
let landed: boolean;

function hooks(extra: Partial<RedictationHooks> = {}): RedictationHooks {
	return {
		recording: () => current,
		anchor: async id => ({ anchor_offset: 4, anchor_text_sha: `sha-${id}` }),
		onRestore: (part) => {
			restored.push(part.id);
		},
		onText: (id, text) => {
			texts.push([id, text]);
			return landed;
		},
		onError: (id, message) => errors.push([id, message]),
		// Like the page: the next append reads the merged file and its length.
		onAudio: (result) => {
			audio.push(result);
			if (result.ok) {
				current = { ...current, audio_file: result.audio_file, duration_seconds: result.duration_seconds };
				merge.total = result.duration_seconds;
			}
		},
		...extra,
	};
}

function setup(extra: Partial<RedictationHooks> = {}): Redictation {
	return scope.run(() => useRedictation(hooks(extra)))!;
}

async function record(r: Redictation, id: string, content = id): Promise<boolean> {
	recorder.stop.mockResolvedValueOnce(new Blob([content], { type: "audio/webm" }));
	expect(await r.start(id)).toBe(true);
	return r.finish();
}

/** What the processor does with a part. */
function processed(id: string, patch: Partial<RecordingPart>): void {
	rows.set(id, { ...rows.get(id)!, ...patch });
}

function stored(id: string, patch: Partial<RecordingPart> = {}): RecordingPart {
	const row: RecordingPart = {
		id,
		recording: "rec-1",
		seq: rows.size + 1,
		status: "pending",
		audio: null,
		language: "de",
		profile: "medical",
		offset_seconds: 10,
		duration_seconds: 6,
		anchor_offset: 4,
		anchor_text_sha: "sha",
		text: null,
		...patch,
	};
	rows.set(id, row);
	return row;
}

function unloadBlocked(): boolean {
	const event = new Event("beforeunload", { cancelable: true });
	window.dispatchEvent(event);
	return event.defaultPrevented;
}

describe("useRedictation", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
		globalThis.fetch = fetchMock as unknown as typeof fetch;
		rows.clear();
		blobs.clear();
		merge.total = 10;
		landed = true;
		current = { id: "rec-1", title: "Visite", audio_file: "f-old", duration_seconds: 10, language: "de", profile: "medical", status: "done", source: "batch" };
		texts.length = 0;
		errors.length = 0;
		audio.length = 0;
		restored.length = 0;
		recorder.sessionId.value = null;
		scope = effectScope();
	});

	afterEach(() => {
		scope.stop();
		vi.useRealTimers();
		globalThis.fetch = realFetch;
	});

	it("stores a stopped part with its anchor, appends its audio with its time range and lands its text", async () => {
		const r = setup();
		recorder.sessionId.value = "s1";
		expect(await record(r, "p1", "p1-audio")).toBe(true);
		// The microphone is free at once; the part is stored and transcribes.
		expect(r.recording.value).toBe(false);
		await vi.advanceTimersByTimeAsync(0);
		expect(store.createPart).toHaveBeenCalledWith(
			{ id: "p1", recording: "rec-1", seq: 1, language: "de", profile: "medical", anchor_offset: 4, anchor_text_sha: "sha-p1" },
			expect.any(Blob),
			"Visite-nachdiktat.webm",
		);
		expect(await store.createPart.mock.calls[0]![1].text()).toBe("p1-audio");
		// Stored: the recorder's IndexedDB copy is not needed any more.
		expect(unfinished.discardUnfinished).toHaveBeenCalledWith("s1");
		// Appended after the 10 s already recorded.
		const merged = merge.mergeToWav.mock.calls[0]![0];
		expect(await Promise.all(merged.map(p => p.text()))).toEqual(["old-audio", "p1-audio"]);
		expect(audio).toEqual([{ partId: "p1", ok: true, audio_file: "f-16", duration_seconds: 16 }]);
		expect(rows.get("p1")).toMatchObject({ offset_seconds: 10, duration_seconds: 6 });
		expect(r.parts.value).toEqual([{ id: "p1", stored: "saved", status: "transcribing", error: null, audio: "saved", audioError: null }]);
		// Nothing is lost by leaving now: the processor works without the page.
		expect(r.busy.value).toBe(false);
		expect(unloadBlocked()).toBe(false);

		await vi.advanceTimersByTimeAsync(2000);
		expect(texts).toEqual([]);
		processed("p1", { status: "done", text: "  Neuer Teil.\n" });
		await vi.advanceTimersByTimeAsync(2000);
		expect(texts).toEqual([["p1", "Neuer Teil."]]);
		expect(rows.get("p1")!.status).toBe("applied");
		expect(r.parts.value).toEqual([]);
	});

	it("numbers parts after the stored ones; audio joins in stop order", async () => {
		stored("old", { status: "applied", seq: 3 });
		const r = setup();
		await record(r, "p1");
		await record(r, "p2");
		await vi.advanceTimersByTimeAsync(0);
		expect(rows.get("p1")!.seq).toBe(4);
		expect(rows.get("p2")!.seq).toBe(5);
		expect(audio.map(a => a.partId)).toEqual(["p1", "p2"]);
		// The second append reads the file the first one stored.
		expect(store.audioUrl.mock.calls[1]![0]).toMatchObject({ audio_file: "f-16" });
		expect(rows.get("p2")).toMatchObject({ offset_seconds: 16, duration_seconds: 6 });
		expect(r.transcribing.value).toBe(2);
	});

	it("a landed part whose save failed stays done until the next save applies it", async () => {
		landed = false;
		const r = setup();
		await record(r, "p1");
		await vi.advanceTimersByTimeAsync(0);
		processed("p1", { status: "done", text: "Text" });
		await vi.advanceTimersByTimeAsync(2000);
		expect(texts).toEqual([["p1", "Text"]]);
		expect(rows.get("p1")!.status).toBe("done");
		r.saved();
		await vi.advanceTimersByTimeAsync(0);
		expect(rows.get("p1")!.status).toBe("applied");
	});

	it("a failed transcription waits; retry asks the processor again", async () => {
		const r = setup();
		await record(r, "p1");
		await vi.advanceTimersByTimeAsync(0);
		processed("p1", { status: "error", error_message: "Verarbeitung fehlgeschlagen: Engine weg" });
		await vi.advanceTimersByTimeAsync(2000);
		expect(errors).toEqual([["p1", "Verarbeitung fehlgeschlagen: Engine weg"]]);
		expect(r.parts.value[0]).toMatchObject({ status: "error", stored: "saved" });
		// Stored: leaving loses nothing.
		expect(r.failed.value).toBe(0);
		expect(unloadBlocked()).toBe(false);

		r.retry("p1");
		await vi.advanceTimersByTimeAsync(0);
		expect(rows.get("p1")!.status).toBe("pending");
		expect(r.parts.value[0]!.status).toBe("transcribing");
		processed("p1", { status: "done", text: "Neu" });
		await vi.advanceTimersByTimeAsync(2000);
		expect(texts).toEqual([["p1", "Neu"]]);
		// Retry transcribes again but never appends the audio a second time.
		expect(audio).toHaveLength(1);
	});

	it("discarding a failed stored part marks it discarded", async () => {
		const r = setup();
		await record(r, "p1");
		await vi.advanceTimersByTimeAsync(0);
		processed("p1", { status: "error", error_message: "kaputt" });
		await vi.advanceTimersByTimeAsync(2000);
		r.discard("p1");
		await vi.advanceTimersByTimeAsync(0);
		expect(rows.get("p1")!.status).toBe("discarded");
		expect(r.parts.value).toEqual([]);
	});

	it("a part that could not be stored lives only in memory until a retry stores it", async () => {
		store.createPart.mockRejectedValueOnce(new Error("offline"));
		const r = setup();
		recorder.sessionId.value = "s1";
		await record(r, "p1", "p1-audio");
		await vi.advanceTimersByTimeAsync(0);
		expect(errors).toEqual([["p1", "Nachdiktat nicht gespeichert: offline"]]);
		expect(r.parts.value[0]).toMatchObject({ stored: "failed", status: "error", audio: "saved" });
		// The recorder's copy stays, so a killed page still offers it.
		expect(unfinished.discardUnfinished).not.toHaveBeenCalled();
		expect(r.failed.value).toBe(1);
		expect(unloadBlocked()).toBe(true);

		r.retry("p1");
		await vi.advanceTimersByTimeAsync(0);
		expect(rows.get("p1")).toMatchObject({ status: "pending", offset_seconds: 10, duration_seconds: 6 });
		expect(unfinished.discardUnfinished).toHaveBeenCalledWith("s1");
		expect(r.failed.value).toBe(0);
		expect(unloadBlocked()).toBe(false);
	});

	it("a failed audio append keeps the part's audio for retryAudio", async () => {
		merge.mergeToWav.mockRejectedValueOnce(new Error("Decoder kaputt"));
		const r = setup();
		await record(r, "p1");
		await vi.advanceTimersByTimeAsync(0);
		expect(audio).toEqual([{ partId: "p1", ok: false, error: "Decoder kaputt" }]);
		expect(r.parts.value[0]).toMatchObject({ audio: "failed", audioError: "Decoder kaputt" });
		expect(rows.get("p1")!.offset_seconds).toBeNull();

		r.retryAudio("p1");
		await vi.advanceTimersByTimeAsync(0);
		expect(audio[1]).toMatchObject({ partId: "p1", ok: true });
		expect(await merge.mergeToWav.mock.calls[1]![0][1]!.text()).toBe("p1");
		expect(rows.get("p1")!.offset_seconds).toBe(10);
	});

	it("opened again: unfinished parts come back, a finished one lands, unappended audio is appended", async () => {
		stored("a", { status: "transcribing", seq: 1 });
		stored("b", { status: "done", seq: 2, text: " Fertig " });
		stored("c", { status: "error", seq: 3, error_message: "kaputt" });
		stored("d", { status: "applied", seq: 4 });
		stored("e", { status: "applied", seq: 5, audio: "file-e", offset_seconds: null, duration_seconds: null });
		blobs.set("file-e", new Blob(["e-audio"]));
		const r = setup();
		await vi.advanceTimersByTimeAsync(0);
		expect(restored).toEqual(["a", "b", "c"]);
		expect(texts).toEqual([["b", "Fertig"]]);
		expect(rows.get("b")!.status).toBe("applied");
		expect(errors).toEqual([["c", "kaputt"]]);
		expect(await merge.mergeToWav.mock.calls[0]![0][1]!.text()).toBe("e-audio");
		expect(rows.get("e")).toMatchObject({ offset_seconds: 10, duration_seconds: 6 });
		expect(r.parts.value.map(p => [p.id, p.status])).toEqual([["a", "transcribing"], ["c", "error"]]);

		processed("a", { status: "done", text: "A" });
		await vi.advanceTimersByTimeAsync(2000);
		expect(texts).toContainEqual(["a", "A"]);
	});

	it("opened again: every chip is back before a finished part's text lands", async () => {
		stored("done-first", { status: "done", seq: 1, text: "Fertig" });
		stored("waiting", { status: "pending", seq: 2 });
		const order: string[] = [];
		setup({
			onRestore: (part) => {
				order.push(`restore:${part.id}`);
			},
			onText: (id) => {
				order.push(`text:${id}`);
				return true;
			},
		});
		await vi.advanceTimersByTimeAsync(0);
		expect(order).toEqual(["restore:done-first", "restore:waiting", "text:done-first"]);
	});

	it("a part landed or removed elsewhere only drops its chip", async () => {
		stored("a", { status: "transcribing" });
		stored("b", { status: "transcribing" });
		setup();
		await vi.advanceTimersByTimeAsync(0);
		processed("a", { status: "applied" });
		rows.delete("b");
		await vi.advanceTimersByTimeAsync(2000);
		expect(texts).toEqual([["a", ""], ["b", ""]]);
		expect(store.updatePart).not.toHaveBeenCalled();
	});

	it("reanchor writes only anchors that changed", async () => {
		const r = setup();
		await record(r, "p1");
		await vi.advanceTimersByTimeAsync(0);
		store.updatePart.mockClear();
		r.reanchor(new Map([["p1", { anchor_offset: 4, anchor_text_sha: "sha-p1" }]]));
		expect(store.updatePart).not.toHaveBeenCalled();
		r.reanchor(new Map([["p1", { anchor_offset: 9, anchor_text_sha: "sha-2" }], ["unknown", { anchor_offset: 1, anchor_text_sha: "x" }]]));
		await vi.advanceTimersByTimeAsync(0);
		expect(store.updatePart).toHaveBeenCalledOnce();
		expect(rows.get("p1")).toMatchObject({ anchor_offset: 9, anchor_text_sha: "sha-2" });
	});

	it("an empty part is not stored", async () => {
		const r = setup();
		recorder.stop.mockResolvedValueOnce(new Blob([], { type: "audio/webm" }));
		await r.start("p1");
		expect(await r.finish()).toBe(false);
		expect(r.startError.value).toBe("Die Aufnahme ist leer.");
		expect(r.parts.value).toEqual([]);
		expect(store.createPart).not.toHaveBeenCalled();
		expect(merge.mergeToWav).not.toHaveBeenCalled();
	});

	it("a microphone failure reports it and leaves the bar closed", async () => {
		recorder.start.mockRejectedValueOnce(new Error("Mikrofon nicht verfügbar"));
		const r = setup();
		expect(await r.start("p1")).toBe(false);
		expect(r.recording.value).toBe(false);
		expect(r.startError.value).toBe("Mikrofon nicht verfügbar");
		expect(await r.finish()).toBe(false);
	});

	it("cancel while the microphone opens drops the part (the recorder releases the late stream)", async () => {
		let open!: (started: boolean) => void;
		recorder.start.mockImplementationOnce(() => new Promise<boolean>((resolve) => {
			open = resolve;
		}));
		const r = setup();
		recorder.sessionId.value = "s3";
		const started = r.start("p1");
		await r.cancel();
		open(false);
		expect(await started).toBe(false);
		expect(recorder.stop).toHaveBeenCalledTimes(1);
		expect(r.recording.value).toBe(false);
		expect(r.parts.value).toEqual([]);
		expect(unfinished.discardUnfinished).toHaveBeenCalledWith("s3");
	});

	it("a cancelled start opening late never stops the next part's recording", async () => {
		let open!: (started: boolean) => void;
		recorder.start.mockImplementationOnce(() => new Promise<boolean>((resolve) => {
			open = resolve;
		}));
		const r = setup();
		const first = r.start("p1");
		await r.cancel();
		expect(await r.start("p2")).toBe(true);
		open(false);
		expect(await first).toBe(false);
		expect(recorder.stop).toHaveBeenCalledTimes(1);
		expect(r.recording.value).toBe(true);
		recorder.stop.mockResolvedValueOnce(new Blob(["p2"], { type: "audio/webm" }));
		expect(await r.finish()).toBe(true);
		expect(r.parts.value.map(p => p.id)).toEqual(["p2"]);
	});

	it("guards page unload while recording or storing", async () => {
		let created!: () => void;
		const r = setup();
		await r.start("p1");
		expect(unloadBlocked()).toBe(true);
		store.createPart.mockImplementationOnce(async (input) => {
			await new Promise<void>((resolve) => {
				created = resolve;
			});
			rows.set(input.id, { ...input, status: "pending", audio: "f", offset_seconds: null });
			return rows.get(input.id)!;
		});
		await r.finish();
		await vi.advanceTimersByTimeAsync(0);
		expect(r.busy.value).toBe(true);
		created();
		await vi.advanceTimersByTimeAsync(0);
		expect(r.busy.value).toBe(false);
		expect(unloadBlocked()).toBe(false);
	});

	it("leaving the page stops polling", async () => {
		const r = setup();
		await record(r, "p1");
		await vi.advanceTimersByTimeAsync(0);
		const polls = store.listParts.mock.calls.length;
		scope.stop();
		processed("p1", { status: "done", text: "spät" });
		await vi.advanceTimersByTimeAsync(10000);
		expect(store.listParts.mock.calls.length).toBeLessThanOrEqual(polls + 1);
		expect(texts).toEqual([]);
		expect(r.parts.value).toHaveLength(1);
	});
});
