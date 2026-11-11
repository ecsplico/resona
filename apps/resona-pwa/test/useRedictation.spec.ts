import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { effectScope, type EffectScope } from "vue";
import { HubError } from "~/composables/useHub";
import { useRedictation, type Redictation, type RedictationAudio } from "~/composables/useRedictation";
import type { Recording } from "~/types/directus";

const hub = vi.hoisted(() => ({
	hasToken: vi.fn(() => true),
	upload: vi.fn(async (_blob: Blob, _name: string) => "up-1"),
	createJob: vi.fn(async (_body: Record<string, unknown>, _key: string) => 42),
	getJob: vi.fn(),
	getResult: vi.fn(),
	deleteJob: vi.fn(async (_id: number) => {}),
}));

const store = vi.hoisted(() => ({
	audioUrl: vi.fn(async (_rec: Recording): Promise<string | null> => "http://d/assets/f-old"),
	replaceAudio: vi.fn(async (_id: string, _audio: Blob, _name: string, seconds: number) => ({ audio_file: `f-${seconds}`, duration_seconds: seconds })),
}));

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

mockNuxtImport("useHub", () => () => hub);
mockNuxtImport("useDataStore", () => () => store);
mockNuxtImport("useRecorder", () => () => recorder);
vi.mock("~/utils/audioMerge", () => ({ mergeToWav: merge.mergeToWav }));

const fetchMock = vi.fn(async (_url: string) => new Response("old-audio", { headers: { "Content-Type": "audio/webm" } }));
const realFetch = globalThis.fetch;

function job(status: string, extra: Record<string, unknown> = {}) {
	return { status, error: null, candidate: null, queue_reason: null, ...extra };
}

let scope: EffectScope;
let current: Recording;
const texts: [string, string][] = [];
const errors: [string, string][] = [];
const audio: RedictationAudio[] = [];

function setup(): Redictation {
	return scope.run(() => useRedictation({
		recording: () => current,
		onText: (id, text) => texts.push([id, text]),
		onError: (id, message) => errors.push([id, message]),
		// Like the page: the next append reads the merged file and its length.
		onAudio: (result) => {
			audio.push(result);
			if (result.ok) {
				current = { ...current, audio_file: result.audio_file, duration_seconds: result.duration_seconds };
				merge.total = result.duration_seconds;
			}
		},
	}))!;
}

async function record(r: Redictation, id: string, content = id): Promise<boolean> {
	recorder.stop.mockResolvedValueOnce(new Blob([content], { type: "audio/webm" }));
	expect(await r.start(id)).toBe(true);
	return r.finish();
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
		hub.hasToken.mockReturnValue(true);
		hub.getJob.mockResolvedValue(job("completed"));
		hub.getResult.mockResolvedValue({ markdown: "  Neuer Teil.\n", segments: [], pages: [], structured: null });
		merge.total = 10;
		current = { id: "rec-1", title: "Visite", audio_file: "f-old", duration_seconds: 10, language: "de", profile: "medical", status: "done", source: "batch" };
		texts.length = 0;
		errors.length = 0;
		audio.length = 0;
		recorder.sessionId.value = null;
		scope = effectScope();
	});

	afterEach(() => {
		scope.stop();
		vi.useRealTimers();
		globalThis.fetch = realFetch;
	});

	it("transcribes a stopped part in the background, appends its audio and deletes the job", async () => {
		hub.getJob.mockResolvedValueOnce(job("processing")).mockResolvedValueOnce(job("completed"));
		const r = setup();
		expect(await r.start("p1")).toBe(true);
		expect(r.recording.value).toBe(true);
		recorder.stop.mockResolvedValueOnce(new Blob(["p1"], { type: "audio/webm" }));
		expect(await r.finish()).toBe(true);
		// The microphone is free at once; the part transcribes.
		expect(r.recording.value).toBe(false);
		expect(r.parts.value).toEqual([{ id: "p1", status: "transcribing", error: null, audio: "appending", audioError: null }]);
		expect(r.busy.value).toBe(true);

		await vi.advanceTimersByTimeAsync(4000);
		expect(texts).toEqual([["p1", "Neuer Teil."]]);
		expect(r.parts.value).toEqual([]);
		expect(r.busy.value).toBe(false);

		const [body, key] = hub.createJob.mock.calls[0]!;
		expect(body).toEqual({ kind: "audio", upload_id: "up-1", language: "de", profile: "medical" });
		expect(key).toMatch(/^redictation:recordings:rec-1:.+/);
		expect(hub.upload.mock.calls[0]![1]).toBe("Visite-nachdiktat.webm");
		expect(hub.deleteJob).toHaveBeenCalledWith(42);

		const parts = merge.mergeToWav.mock.calls[0]![0];
		expect(await Promise.all(parts.map(p => p.text()))).toEqual(["old-audio", "p1"]);
		expect(store.replaceAudio).toHaveBeenCalledWith("rec-1", expect.any(Blob), "Visite.wav", 16);
		expect(audio).toEqual([{ partId: "p1", ok: true, audio_file: "f-16", duration_seconds: 16 }]);
	});

	it("drops a part's IndexedDB copy only once its audio is in the recording, or when it is given up", async () => {
		const r = setup();
		recorder.sessionId.value = "s1";
		await r.start("p1");
		await r.finish();
		expect(unfinished.discardUnfinished).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(2000);
		expect(unfinished.discardUnfinished).toHaveBeenCalledWith("s1");

		// Append fails: the copy stays (a killed page still offers it) until the part is discarded.
		unfinished.discardUnfinished.mockClear();
		store.replaceAudio.mockRejectedValueOnce(new Error("offline"));
		recorder.sessionId.value = "s2";
		await r.start("p2");
		await r.finish();
		await vi.advanceTimersByTimeAsync(2000);
		expect(r.parts.value.find(p => p.id === "p2")?.audio).toBe("failed");
		expect(unfinished.discardUnfinished).not.toHaveBeenCalled();
		r.discard("p2");
		expect(unfinished.discardUnfinished).toHaveBeenCalledWith("s2");

		// Verwerfen while recording deletes the copy at once.
		recorder.sessionId.value = "s3";
		await r.start("p3");
		await r.cancel();
		expect(unfinished.discardUnfinished).toHaveBeenCalledWith("s3");
	});

	it("records the next part while the first transcribes; audio joins in stop order", async () => {
		const r = setup();
		await record(r, "p1");
		await record(r, "p2");
		expect(r.transcribing.value).toBe(2);

		await vi.advanceTimersByTimeAsync(2000);
		expect(texts.map(([id]) => id).sort()).toEqual(["p1", "p2"]);
		expect(audio.map(a => a.partId)).toEqual(["p1", "p2"]);
		// The second append reads the file the first one stored.
		expect(store.audioUrl.mock.calls[1]![0]).toMatchObject({ audio_file: "f-16" });
		expect(audio[1]).toMatchObject({ ok: true, audio_file: "f-22", duration_seconds: 22 });
	});

	it("keeps polling through transient hub errors", async () => {
		hub.getJob.mockRejectedValueOnce(new HubError(503, "http_503", "busy")).mockResolvedValueOnce(job("completed"));
		const r = setup();
		await record(r, "p1");
		await vi.advanceTimersByTimeAsync(4000);
		expect(texts).toEqual([["p1", "Neuer Teil."]]);
		expect(errors).toEqual([]);
	});

	it("a failed job keeps the part for retry, which uses a fresh job", async () => {
		hub.getJob.mockResolvedValueOnce(job("failed", { error: { code: "x", message: "Engine weg" } }));
		const r = setup();
		await record(r, "p1");
		await vi.advanceTimersByTimeAsync(2000);
		expect(errors).toEqual([["p1", "Verarbeitung fehlgeschlagen: Engine weg"]]);
		expect(r.parts.value).toEqual([{ id: "p1", status: "error", error: "Verarbeitung fehlgeschlagen: Engine weg", audio: "saved", audioError: null }]);
		expect(texts).toEqual([]);
		// The audio is in the recording regardless.
		expect(audio).toHaveLength(1);
		expect(r.busy.value).toBe(false);
		// The failed job was deleted on the hub.
		expect(hub.deleteJob).toHaveBeenCalledWith(42);
		// The part lives only in memory: leaving would lose it.
		expect(r.failed.value).toBe(1);
		expect(unloadBlocked()).toBe(true);

		r.retry("p1");
		expect(r.parts.value[0]!.status).toBe("transcribing");
		await vi.advanceTimersByTimeAsync(2000);
		expect(texts).toEqual([["p1", "Neuer Teil."]]);
		expect(r.parts.value).toEqual([]);
		const keys = hub.createJob.mock.calls.map(c => c[1]);
		expect(keys).toHaveLength(2);
		expect(keys[0]).not.toBe(keys[1]);
		expect(await hub.upload.mock.calls[1]![0].text()).toBe("p1");
		// Retry transcribes again but never appends the audio a second time.
		expect(audio).toHaveLength(1);
	});

	it("discard drops a failed part", async () => {
		hub.hasToken.mockReturnValue(false);
		const r = setup();
		await record(r, "p1");
		await vi.advanceTimersByTimeAsync(0);
		expect(errors[0]![1]).toMatch(/^Kein Hub-Token/);
		expect(hub.upload).not.toHaveBeenCalled();
		r.discard("p1");
		expect(r.parts.value).toEqual([]);
		hub.hasToken.mockReturnValue(true);
		r.retry("p1");
		expect(hub.upload).not.toHaveBeenCalled();
	});

	it("reports an unreachable hub", async () => {
		hub.upload.mockRejectedValueOnce(new HubError(0, "network", "Failed to fetch"));
		const r = setup();
		await record(r, "p1");
		await vi.advanceTimersByTimeAsync(0);
		expect(errors).toEqual([["p1", "Hub nicht erreichbar – Verbindung prüfen und erneut versuchen."]]);
	});

	it("a failed audio append lands the text but keeps the part's audio for retryAudio", async () => {
		merge.mergeToWav.mockRejectedValueOnce(new Error("Decoder kaputt"));
		const r = setup();
		await record(r, "p1");
		await vi.advanceTimersByTimeAsync(2000);
		expect(audio).toEqual([{ partId: "p1", ok: false, error: "Decoder kaputt" }]);
		expect(texts).toEqual([["p1", "Neuer Teil."]]);
		expect(r.parts.value).toEqual([{ id: "p1", status: "done", error: null, audio: "failed", audioError: "Decoder kaputt" }]);
		expect(r.failed.value).toBe(1);
		expect(unloadBlocked()).toBe(true);

		r.retryAudio("p1");
		await vi.advanceTimersByTimeAsync(0);
		expect(audio[1]).toMatchObject({ partId: "p1", ok: true });
		const parts = merge.mergeToWav.mock.calls[1]![0];
		expect(await parts[1]!.text()).toBe("p1");
		expect(r.parts.value).toEqual([]);
		expect(r.failed.value).toBe(0);
		expect(unloadBlocked()).toBe(false);
	});

	it("a throwing onAudio never breaks later appends", async () => {
		const r = scope.run(() => useRedictation({
			recording: () => current,
			onText: (id, text) => texts.push([id, text]),
			onError: (id, message) => errors.push([id, message]),
			onAudio: (result) => {
				audio.push(result);
				if (result.ok && result.partId === "p1") throw new Error("Seite kaputt");
			},
		}))!;
		await record(r, "p1");
		await record(r, "p2");
		await vi.advanceTimersByTimeAsync(2000);
		// p1's append was stored, then reported as failed through the same hook.
		expect(audio.map(a => [a.partId, a.ok])).toEqual([["p1", true], ["p1", false], ["p2", true]]);
		expect(audio[1]).toMatchObject({ error: "Seite kaputt" });
		expect(r.merging.value).toBe(false);
		expect(r.parts.value).toEqual([]);
	});

	it("a non-transient result read failure fails the part and still deletes the job", async () => {
		hub.getResult.mockRejectedValueOnce(new HubError(410, "result_expired", "weg"));
		const r = setup();
		await record(r, "p1");
		await vi.advanceTimersByTimeAsync(2000);
		expect(errors).toEqual([["p1", "Ergebnis auf dem Hub abgelaufen"]]);
		expect(hub.deleteJob).toHaveBeenCalledWith(42);
	});

	it("a part whose recording is gone fails instead of staying in transcribing", async () => {
		const r = setup();
		recorder.stop.mockResolvedValueOnce(new Blob(["p1"], { type: "audio/webm" }));
		await r.start("p1");
		const open = current;
		current = null as unknown as Recording;
		expect(await r.finish()).toBe(true);
		await vi.advanceTimersByTimeAsync(0);
		expect(errors).toEqual([["p1", "Die Aufnahme ist nicht mehr geöffnet."]]);
		expect(r.parts.value[0]).toMatchObject({ status: "error", audio: "failed" });
		expect(r.transcribing.value).toBe(0);
		current = open;
	});

	it("an empty part is not transcribed", async () => {
		const r = setup();
		recorder.stop.mockResolvedValueOnce(new Blob([], { type: "audio/webm" }));
		await r.start("p1");
		expect(await r.finish()).toBe(false);
		expect(r.startError.value).toBe("Die Aufnahme ist leer.");
		expect(r.parts.value).toEqual([]);
		expect(hub.upload).not.toHaveBeenCalled();
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
		const started = r.start("p1");
		await r.cancel();
		// The cancel's recorder.stop() made the pending start stale.
		open(false);
		expect(await started).toBe(false);
		expect(recorder.stop).toHaveBeenCalledTimes(1);
		expect(r.recording.value).toBe(false);
		expect(r.parts.value).toEqual([]);
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

	it("guards page unload while busy", async () => {
		const r = setup();
		await r.start("p1");
		await vi.advanceTimersByTimeAsync(0);
		expect(unloadBlocked()).toBe(true);
		await r.finish();
		await vi.advanceTimersByTimeAsync(2000);
		expect(r.busy.value).toBe(false);
		expect(unloadBlocked()).toBe(false);
	});

	it("leaving the page stops polling and deletes the running job", async () => {
		hub.getJob.mockResolvedValue(job("processing"));
		const r = setup();
		await record(r, "p1");
		await vi.advanceTimersByTimeAsync(2000);
		scope.stop();
		await vi.advanceTimersByTimeAsync(10000);
		expect(hub.getJob).toHaveBeenCalledTimes(1);
		expect(hub.deleteJob).toHaveBeenCalledWith(42);
		expect(texts).toEqual([]);
	});
});
