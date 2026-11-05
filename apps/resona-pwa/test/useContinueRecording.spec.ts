import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { effectScope, type EffectScope } from "vue";
import { HubError } from "~/composables/useHub";
import { useContinueRecording, type ContinueRecording } from "~/composables/useContinueRecording";
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
	audioUrl: vi.fn(async (_rec: unknown): Promise<string | null> => "http://d/assets/f-old"),
	replaceAudio: vi.fn(async (_id: string, _audio: Blob, _name: string, seconds: number) => ({ audio_file: "f-new", duration_seconds: seconds })),
}));

const recorder = vi.hoisted(() => ({
	start: vi.fn(async () => {}),
	stop: vi.fn(async (): Promise<Blob> => new Blob(["new-part"], { type: "audio/webm" })),
}));

const merge = vi.hoisted(() => ({
	mergeToWav: vi.fn(async (_parts: Blob[]) => ({ blob: new Blob(["wav"], { type: "audio/wav" }), seconds: 16 })),
}));

mockNuxtImport("useHub", () => () => hub);
mockNuxtImport("useDataStore", () => () => store);
mockNuxtImport("useRecorder", () => () => recorder);
vi.mock("~/utils/audioMerge", () => merge);

const fetchMock = vi.fn(async (_url: string) => new Response("old-audio", { headers: { "Content-Type": "audio/webm" } }));
const realFetch = globalThis.fetch;

function rec(extra: Partial<Recording> = {}): Recording {
	return {
		id: "rec-1",
		title: "Visite",
		audio_file: "f-old",
		duration_seconds: 10,
		language: "de",
		profile: "medical",
		status: "done",
		source: "batch",
		...extra,
	};
}

function job(status: string, extra: Record<string, unknown> = {}) {
	return { status, error: null, candidate: null, queue_reason: null, ...extra };
}

const RESULT = { markdown: "  Neuer Teil.\n", segments: [], pages: [], structured: null };

let scope: EffectScope;

function setup() {
	return scope.run(() => useContinueRecording())!;
}

/** Records a part and finishes it; the returned promise settles once polling ends. */
async function recordAndFinish(cont: ContinueRecording, recording = rec()) {
	await cont.start();
	return cont.finish(recording);
}

function unloadBlocked(): boolean {
	const event = new Event("beforeunload", { cancelable: true });
	window.dispatchEvent(event);
	return event.defaultPrevented;
}

describe("useContinueRecording", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
		globalThis.fetch = fetchMock as unknown as typeof fetch;
		hub.hasToken.mockReturnValue(true);
		hub.getJob.mockResolvedValue(job("completed"));
		hub.getResult.mockResolvedValue(RESULT);
		scope = effectScope();
	});

	afterEach(() => {
		scope.stop();
		vi.useRealTimers();
		globalThis.fetch = realFetch;
	});

	it("transcribes the new part via a hub job, merges the audio and deletes the job", async () => {
		hub.getJob.mockResolvedValueOnce(job("processing")).mockResolvedValueOnce(job("completed"));
		const cont = setup();
		await cont.start();
		expect(recorder.start).toHaveBeenCalledOnce();
		expect(cont.phase.value).toBe("recording");

		const done = cont.finish(rec());
		await vi.advanceTimersByTimeAsync(0);
		expect(cont.phase.value).toBe("transcribing");
		await vi.advanceTimersByTimeAsync(4000);
		const result = await done;

		expect(result).toEqual({ text: "Neuer Teil.", audio_file: "f-new", duration_seconds: 16, audioMerged: true, mergeError: null });
		const [part, name] = hub.upload.mock.calls[0]!;
		expect(await part.text()).toBe("new-part");
		expect(name).toMatch(/\.webm$/);
		const [body, key] = hub.createJob.mock.calls[0]!;
		expect(body).toEqual({ kind: "audio", upload_id: "up-1", language: "de", profile: "medical" });
		expect(key).toMatch(/^continue:recordings:rec-1:.+/);
		expect(hub.getJob).toHaveBeenCalledTimes(2);
		expect(hub.getResult).toHaveBeenCalledWith(42);
		expect(hub.deleteJob).toHaveBeenCalledWith(42);

		expect(fetchMock).toHaveBeenCalledWith("http://d/assets/f-old");
		const parts = merge.mergeToWav.mock.calls[0]![0];
		expect(await Promise.all(parts.map(p => p.text()))).toEqual(["old-audio", "new-part"]);
		expect(store.replaceAudio).toHaveBeenCalledWith("rec-1", expect.any(Blob), "Visite.wav", 16);

		expect(cont.phase.value).toBe("idle");
		expect(cont.error.value).toBeNull();
		expect(cont.hasRetainedPart.value).toBe(false);
	});

	it("ignores a failed job cleanup", async () => {
		hub.deleteJob.mockRejectedValueOnce(new HubError(500, "x", "kaputt"));
		const cont = setup();
		const done = recordAndFinish(cont);
		await vi.advanceTimersByTimeAsync(2000);
		expect((await done)!.text).toBe("Neuer Teil.");
		expect(cont.phase.value).toBe("idle");
	});

	it("keeps polling through transient hub errors", async () => {
		hub.getJob.mockRejectedValueOnce(new HubError(503, "http_503", "busy")).mockResolvedValueOnce(job("completed"));
		const cont = setup();
		const done = recordAndFinish(cont);
		await vi.advanceTimersByTimeAsync(4000);
		expect((await done)!.text).toBe("Neuer Teil.");
		expect(hub.getJob).toHaveBeenCalledTimes(2);
	});

	it("keeps the part when the job fails, and retry succeeds without re-recording", async () => {
		hub.getJob.mockResolvedValueOnce(job("failed", { error: { code: "engine", message: "Engine abgestürzt" } }));
		const cont = setup();
		const first = recordAndFinish(cont);
		await vi.advanceTimersByTimeAsync(2000);
		expect(await first).toBeNull();
		expect(cont.phase.value).toBe("error");
		expect(cont.error.value).toBe("Verarbeitung fehlgeschlagen: Engine abgestürzt");
		expect(cont.hasRetainedPart.value).toBe(true);
		expect(store.replaceAudio).not.toHaveBeenCalled();

		const second = cont.retry(rec());
		await vi.advanceTimersByTimeAsync(2000);
		const result = await second;

		expect(result).toMatchObject({ text: "Neuer Teil.", audio_file: "f-new", audioMerged: true });
		expect(recorder.start).toHaveBeenCalledOnce();
		expect(recorder.stop).toHaveBeenCalledOnce();
		expect(hub.upload).toHaveBeenCalledTimes(2);
		expect(await hub.upload.mock.calls[1]![0].text()).toBe("new-part");
		const keys = hub.createJob.mock.calls.map(c => c[1]);
		expect(keys[0]).not.toBe(keys[1]);
		expect(cont.hasRetainedPart.value).toBe(false);
	});

	it("keeps the part when the upload fails", async () => {
		hub.upload.mockRejectedValueOnce(new HubError(0, "network", "Failed to fetch"));
		const cont = setup();
		expect(await recordAndFinish(cont)).toBeNull();
		expect(cont.phase.value).toBe("error");
		expect(cont.error.value).toContain("Failed to fetch");
		expect(cont.hasRetainedPart.value).toBe(true);
	});

	it("still returns the text when the audio merge fails", async () => {
		merge.mergeToWav.mockRejectedValueOnce(new Error("Audio konnte nicht dekodiert werden."));
		const cont = setup();
		const done = recordAndFinish(cont);
		await vi.advanceTimersByTimeAsync(2000);

		expect(await done).toEqual({
			text: "Neuer Teil.",
			audio_file: null,
			duration_seconds: 10,
			audioMerged: false,
			mergeError: "Audio konnte nicht dekodiert werden.",
		});
		expect(store.replaceAudio).not.toHaveBeenCalled();
		expect(cont.phase.value).toBe("idle");
		expect(cont.hasRetainedPart.value).toBe(false);
	});

	it("still returns the text when storing the merged audio fails", async () => {
		store.replaceAudio.mockRejectedValueOnce(new Error("forbidden"));
		const cont = setup();
		const done = recordAndFinish(cont);
		await vi.advanceTimersByTimeAsync(2000);
		const result = await done;
		expect(result).toMatchObject({ text: "Neuer Teil.", audio_file: null, duration_seconds: 10, audioMerged: false });
		expect(result!.mergeError).toBe("Speichern fehlgeschlagen: forbidden");
	});

	it("stores the new part alone when the recording has no audio", async () => {
		store.audioUrl.mockResolvedValueOnce(null);
		const cont = setup();
		const done = recordAndFinish(cont, rec({ audio_file: undefined, duration_seconds: undefined, title: "" }));
		await vi.advanceTimersByTimeAsync(2000);
		expect(await done).toMatchObject({ audioMerged: true, duration_seconds: 16 });
		expect(fetchMock).not.toHaveBeenCalled();
		const parts = merge.mergeToWav.mock.calls[0]![0];
		expect(await Promise.all(parts.map(p => p.text()))).toEqual(["new-part"]);
		expect(store.replaceAudio).toHaveBeenCalledWith("rec-1", expect.any(Blob), "rec-1.wav", 16);
	});

	it("revokes the blob: URL it got for the stored audio", async () => {
		store.audioUrl.mockResolvedValueOnce("blob:local-audio");
		const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
		const cont = setup();
		const done = recordAndFinish(cont);
		await vi.advanceTimersByTimeAsync(2000);
		await done;
		expect(fetchMock).toHaveBeenCalledWith("blob:local-audio");
		expect(revoke).toHaveBeenCalledWith("blob:local-audio");
		revoke.mockRestore();
	});

	it("rejects an empty recording", async () => {
		recorder.stop.mockResolvedValueOnce(new Blob([], { type: "audio/webm" }));
		const cont = setup();
		expect(await recordAndFinish(cont)).toBeNull();
		expect(cont.phase.value).toBe("error");
		expect(cont.error.value).toBe("Die Aufnahme ist leer.");
		expect(cont.hasRetainedPart.value).toBe(false);
		expect(hub.upload).not.toHaveBeenCalled();
	});

	it("keeps the part without a hub token and points to the settings", async () => {
		hub.hasToken.mockReturnValue(false);
		const cont = setup();
		expect(await recordAndFinish(cont)).toBeNull();
		expect(cont.phase.value).toBe("error");
		expect(cont.error.value).toContain("Einstellungen → Verbindung");
		expect(cont.hasRetainedPart.value).toBe(true);
		expect(hub.upload).not.toHaveBeenCalled();

		hub.hasToken.mockReturnValue(true);
		const done = cont.retry(rec());
		await vi.advanceTimersByTimeAsync(2000);
		expect((await done)!.text).toBe("Neuer Teil.");
	});

	it("reports a microphone failure on start", async () => {
		recorder.start.mockRejectedValueOnce(new Error("Mikrofonzugriff wurde verweigert."));
		const cont = setup();
		await cont.start();
		expect(cont.phase.value).toBe("error");
		expect(cont.error.value).toBe("Mikrofonzugriff wurde verweigert.");
		expect(cont.hasRetainedPart.value).toBe(false);
	});

	it("cancel while transcribing stops polling and drops the part", async () => {
		hub.getJob.mockResolvedValue(job("processing"));
		const cont = setup();
		const done = recordAndFinish(cont);
		await vi.advanceTimersByTimeAsync(2000);
		expect(hub.getJob).toHaveBeenCalledOnce();

		await cont.cancel();
		expect(await done).toBeNull();
		await vi.advanceTimersByTimeAsync(10_000);
		expect(hub.getJob).toHaveBeenCalledOnce();
		expect(hub.getResult).not.toHaveBeenCalled();
		expect(hub.deleteJob).toHaveBeenCalledWith(42);
		expect(cont.phase.value).toBe("idle");
		expect(cont.hasRetainedPart.value).toBe(false);
	});

	it("cancel while recording discards the recording", async () => {
		const cont = setup();
		await cont.start();
		await cont.cancel();
		expect(recorder.stop).toHaveBeenCalledOnce();
		expect(cont.phase.value).toBe("idle");
		expect(hub.upload).not.toHaveBeenCalled();
	});

	it("discard drops a retained part", async () => {
		hub.hasToken.mockReturnValue(false);
		const cont = setup();
		await recordAndFinish(cont);
		cont.discard();
		expect(cont.phase.value).toBe("idle");
		expect(cont.error.value).toBeNull();
		expect(cont.hasRetainedPart.value).toBe(false);
		expect(await cont.retry(rec())).toBeNull();
	});

	it("guards page unload only while recording, transcribing or merging", async () => {
		hub.getJob.mockResolvedValueOnce(job("processing")).mockResolvedValueOnce(job("completed"));
		const cont = setup();
		expect(unloadBlocked()).toBe(false);
		await cont.start();
		expect(unloadBlocked()).toBe(true);
		const done = cont.finish(rec());
		await vi.advanceTimersByTimeAsync(2000);
		expect(cont.phase.value).toBe("transcribing");
		expect(unloadBlocked()).toBe(true);
		await vi.advanceTimersByTimeAsync(2000);
		await done;
		expect(unloadBlocked()).toBe(false);

		await cont.start();
		expect(unloadBlocked()).toBe(true);
		scope.stop();
		expect(unloadBlocked()).toBe(false);
	});
});
