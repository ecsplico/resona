import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { effectScope } from "vue";
import { RECORDER_TIMESLICE_MS, useRecorder } from "~/composables/useRecorder";

const unfinished = vi.hoisted(() => ({
	beginUnfinished: vi.fn(async (_meta: Record<string, unknown>) => {}),
	appendUnfinishedChunk: vi.fn(async (_id: string, _part: number, _seq: number, _chunk: Blob, _elapsed: number) => {}),
	claimUnfinished: vi.fn((_id: string) => {}),
	releaseUnfinished: vi.fn((_id: string) => {}),
}));
vi.mock("~/data/unfinishedRecordings", () => unfinished);

const wake = vi.hoisted(() => ({ acquire: vi.fn(), release: vi.fn() }));
vi.mock("~/composables/useWakeLock", () => ({ useWakeLock: () => ({ held: { value: false }, ...wake }) }));

const merge = vi.hoisted(() => ({
	mergeToWav: vi.fn(async (_parts: Blob[]) => ({ blob: new Blob(["merged"], { type: "audio/wav" }), seconds: 9 })),
}));
vi.mock("~/utils/audioMerge", () => merge);

// A minimal fake MediaRecorder that mirrors the subset of the API the
// composable relies on. happy-dom implements none of it.
class FakeMediaRecorder {
	static instances: FakeMediaRecorder[] = [];
	static isTypeSupported(type: string): boolean {
		return type === "audio/webm;codecs=opus" || type === "audio/webm";
	}

	state: "inactive" | "recording" | "paused" = "inactive";
	stream: MediaStream;
	ondataavailable: ((e: { data: Blob }) => void) | null = null;
	onstop: (() => void) | null = null;

	constructor(stream: MediaStream) {
		this.stream = stream;
		FakeMediaRecorder.instances.push(this);
	}

	timeslice: number | undefined;

	start(timeslice?: number): void {
		this.timeslice = timeslice;
		this.state = "recording";
	}

	/** A timeslice elapsed: the browser hands over the audio so far. */
	emit(text: string): void {
		this.ondataavailable?.({ data: new Blob([text], { type: "audio/webm" }) });
	}

	pause(): void {
		this.state = "paused";
	}

	resume(): void {
		this.state = "recording";
	}

	stop(): void {
		this.state = "inactive";
		this.ondataavailable?.({ data: new Blob(["chunk"], { type: "audio/webm" }) });
		this.onstop?.();
	}
}

function fakeStream(): MediaStream {
	const track = { stop: vi.fn(), kind: "audio" };
	return {
		getTracks: () => [track as unknown as MediaStreamTrack],
	} as unknown as MediaStream;
}

const getUserMedia = vi.fn(async () => fakeStream());

describe("useRecorder", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		vi.useFakeTimers();
		FakeMediaRecorder.instances = [];

		globalThis.MediaRecorder = FakeMediaRecorder as unknown as typeof MediaRecorder;
		Object.defineProperty(globalThis.navigator, "mediaDevices", {
			value: { getUserMedia },
			configurable: true,
		});
		getUserMedia.mockImplementation(async () => fakeStream());
		// AudioContext stays undefined in happy-dom: levels must degrade gracefully.
		delete (globalThis as Record<string, unknown>).AudioContext;
		delete (globalThis as Record<string, unknown>).webkitAudioContext;
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it("start() requests the mic and sets recording true", async () => {
		const r = useRecorder();
		await r.start();

		expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
		expect(r.recording.value).toBe(true);
		expect(r.paused.value).toBe(false);
	});

	it("start(stream) reuses a provided stream and does not open the mic", async () => {
		const r = useRecorder();
		const shared = fakeStream();
		await r.start(shared);

		expect(getUserMedia).not.toHaveBeenCalled();
		expect(r.recording.value).toBe(true);
	});

	it("pause() and resume() flip paused", async () => {
		const r = useRecorder();
		await r.start();

		r.pause();
		expect(r.paused.value).toBe(true);

		r.resume();
		expect(r.paused.value).toBe(false);
	});

	it("stop() resolves to a Blob and sets recording false", async () => {
		const r = useRecorder();
		await r.start();

		const blob = await r.stop();
		expect(blob).toBeInstanceOf(Blob);
		expect(blob.type).toBe("audio/webm;codecs=opus");
		expect(r.recording.value).toBe(false);
	});

	it("elapsed increments while recording", async () => {
		const r = useRecorder();
		await r.start();

		expect(r.elapsed.value).toBe(0);
		await vi.advanceTimersByTimeAsync(3000);
		expect(r.elapsed.value).toBeGreaterThanOrEqual(3);

		await r.stop();
	});

	it("elapsed does not advance while paused", async () => {
		const r = useRecorder();
		await r.start();

		await vi.advanceTimersByTimeAsync(1000);
		const atPause = r.elapsed.value;
		r.pause();
		await vi.advanceTimersByTimeAsync(5000);
		expect(r.elapsed.value).toBe(atPause);

		await r.stop();
	});

	it("levels is an array (empty when AudioContext absent)", async () => {
		const r = useRecorder();
		await r.start();

		expect(Array.isArray(r.levels.value)).toBe(true);

		await r.stop();
	});

	it("stop() stops tracks of an owned stream", async () => {
		const r = useRecorder();
		await r.start();
		const stream = FakeMediaRecorder.instances[0]!.stream;
		const track = stream.getTracks()[0]!;

		await r.stop();
		expect(track.stop).toHaveBeenCalled();
	});

	it("stop() does NOT stop tracks of a caller-provided stream", async () => {
		const r = useRecorder();
		const shared = fakeStream();
		await r.start(shared);
		const track = shared.getTracks()[0]!;

		await r.stop();
		expect(track.stop).not.toHaveBeenCalled();
	});

	it("onScopeDispose cleans up an owned stream", async () => {
		const scope = effectScope();
		let r!: ReturnType<typeof useRecorder>;
		let track!: MediaStreamTrack;
		await scope.run(async () => {
			r = useRecorder();
			await r.start();
			track = FakeMediaRecorder.instances[0]!.stream.getTracks()[0]!;
		});

		scope.stop();
		expect(track.stop).toHaveBeenCalled();
	});

	it("start() rejects with the secure-context hint when mediaDevices is missing", async () => {
		Object.defineProperty(globalThis.navigator, "mediaDevices", { value: undefined, configurable: true });
		const r = useRecorder();

		await expect(r.start()).rejects.toThrow("Mikrofon nicht verfügbar – die App muss über HTTPS oder localhost geöffnet sein.");
		expect(r.recording.value).toBe(false);
		expect(FakeMediaRecorder.instances).toHaveLength(0);
	});

	it("start() rejects with a permission message when the mic is denied", async () => {
		getUserMedia.mockImplementation(async () => {
			throw new DOMException("denied", "NotAllowedError");
		});
		const r = useRecorder();

		await expect(r.start()).rejects.toThrow("Mikrofonzugriff wurde verweigert.");
		expect(r.recording.value).toBe(false);
	});

	it("a start whose microphone opens after stop() releases that stream and does not record", async () => {
		const late = fakeStream();
		let open!: (s: MediaStream) => void;
		getUserMedia.mockImplementationOnce(() => new Promise<MediaStream>((resolve) => {
			open = resolve;
		}));
		const r = useRecorder();
		const started = r.start();
		await r.stop();
		open(late);

		expect(await started).toBe(false);
		expect(late.getTracks()[0]!.stop).toHaveBeenCalled();
		expect(r.recording.value).toBe(false);
		expect(FakeMediaRecorder.instances).toHaveLength(0);
	});

	it("an older start opening late never replaces a newer recording", async () => {
		const late = fakeStream();
		let open!: (s: MediaStream) => void;
		getUserMedia.mockImplementationOnce(() => new Promise<MediaStream>((resolve) => {
			open = resolve;
		}));
		const r = useRecorder();
		const first = r.start();
		await r.stop();
		expect(await r.start()).toBe(true);
		const current = FakeMediaRecorder.instances[0]!;
		open(late);

		expect(await first).toBe(false);
		expect(late.getTracks()[0]!.stop).toHaveBeenCalled();
		expect(FakeMediaRecorder.instances).toHaveLength(1);
		expect(current.state).toBe("recording");
		expect(current.stream.getTracks()[0]!.stop).not.toHaveBeenCalled();
		expect(r.recording.value).toBe(true);
	});
});

/** A stream whose single track can fire `mute`/`ended` like a real MediaStreamTrack. */
function listenableStream(): { stream: MediaStream; track: EventTarget & { stop: ReturnType<typeof vi.fn> } } {
	const track = Object.assign(new EventTarget(), { stop: vi.fn(), kind: "audio" });
	return { stream: { getTracks: () => [track as unknown as MediaStreamTrack] } as unknown as MediaStream, track };
}

describe("useRecorder: persistence, interruptions, wake lock", () => {
	let visibility: DocumentVisibilityState = "visible";

	beforeEach(() => {
		vi.clearAllMocks();
		vi.useFakeTimers();
		FakeMediaRecorder.instances = [];
		globalThis.MediaRecorder = FakeMediaRecorder as unknown as typeof MediaRecorder;
		Object.defineProperty(globalThis.navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
		getUserMedia.mockImplementation(async () => listenableStream().stream);
		delete (globalThis as Record<string, unknown>).AudioContext;
		delete (globalThis as Record<string, unknown>).webkitAudioContext;
		visibility = "visible";
		Object.defineProperty(document, "visibilityState", { get: () => visibility, configurable: true });
	});

	afterEach(() => {
		vi.useRealTimers();
		Reflect.deleteProperty(document, "visibilityState");
	});

	it("records in timeslices and writes every chunk to IndexedDB under its session", async () => {
		const r = useRecorder({ persist: () => ({ kind: "record", label: "Visite" }) });
		await r.start();
		const rec = FakeMediaRecorder.instances[0]!;
		expect(rec.timeslice).toBe(RECORDER_TIMESLICE_MS);
		const id = r.sessionId.value!;
		expect(unfinished.claimUnfinished).toHaveBeenCalledWith(id);

		await vi.advanceTimersByTimeAsync(4000);
		rec.emit("a");
		const blob = await r.stop();

		expect(unfinished.beginUnfinished).toHaveBeenCalledWith(expect.objectContaining({ id, kind: "record", label: "Visite", mime: "audio/webm;codecs=opus" }));
		expect(unfinished.appendUnfinishedChunk.mock.calls.map(c => [c[0], c[1], c[2], c[4]])).toEqual([[id, 0, 0, 4], [id, 0, 1, 4]]);
		// The caller discards the session once the audio is saved; stop keeps it.
		expect(r.sessionId.value).toBe(id);
		expect(await blob.text()).toBe("achunk");
	});

	it("without persist nothing goes to IndexedDB", async () => {
		const r = useRecorder();
		await r.start();
		FakeMediaRecorder.instances[0]!.emit("a");
		await r.stop();
		expect(r.sessionId.value).toBeNull();
		expect(unfinished.beginUnfinished).not.toHaveBeenCalled();
		expect(unfinished.appendUnfinishedChunk).not.toHaveBeenCalled();
	});

	it("a muted track interrupts; continuing records a new part and stop returns one recording", async () => {
		const first = listenableStream();
		getUserMedia.mockImplementationOnce(async () => first.stream);
		const r = useRecorder({ persist: () => ({ kind: "record" }) });
		await r.start();
		await vi.advanceTimersByTimeAsync(2000);

		first.track.dispatchEvent(new Event("mute"));
		await vi.waitFor(() => expect(r.interrupted.value).toBe("muted"));
		expect(r.recording.value).toBe(true);
		expect(r.interruption.value).toContain("Bisher Aufgenommenes bleibt erhalten.");
		expect(FakeMediaRecorder.instances[0]!.state).toBe("inactive");
		expect(first.track.stop).toHaveBeenCalled();
		// Nothing is recorded meanwhile, so the clock stands still.
		await vi.advanceTimersByTimeAsync(5000);
		expect(r.elapsed.value).toBe(2);
		// Pause is not offered while interrupted.
		r.pause();
		expect(r.paused.value).toBe(false);

		expect(await r.continueRecording()).toBe(true);
		expect(r.interrupted.value).toBeNull();
		expect(getUserMedia).toHaveBeenCalledTimes(2);
		const second = FakeMediaRecorder.instances[1]!;
		expect(second.state).toBe("recording");
		second.emit("b");
		await vi.advanceTimersByTimeAsync(1000);
		expect(r.elapsed.value).toBe(3);

		const blob = await r.stop();
		expect(merge.mergeToWav).toHaveBeenCalledTimes(1);
		const parts = merge.mergeToWav.mock.calls[0]![0];
		expect(await Promise.all(parts.map(p => p.text()))).toEqual(["chunk", "bchunk"]);
		expect(blob.type).toBe("audio/wav");
		expect(r.recording.value).toBe(false);
		// Both parts went to the same session, part by part.
		const id = r.sessionId.value;
		expect(unfinished.appendUnfinishedChunk.mock.calls.map(c => [c[0], c[1]])).toEqual([[id, 0], [id, 1], [id, 1]]);
	});

	it("an ended track interrupts too, and stop then returns what was recorded", async () => {
		const first = listenableStream();
		getUserMedia.mockImplementationOnce(async () => first.stream);
		const r = useRecorder();
		await r.start();
		FakeMediaRecorder.instances[0]!.emit("a");
		first.track.dispatchEvent(new Event("ended"));
		await vi.waitFor(() => expect(r.interrupted.value).toBe("ended"));

		const blob = await r.stop();
		expect(await blob.text()).toBe("achunk");
		expect(merge.mergeToWav).not.toHaveBeenCalled();
		expect(r.interrupted.value).toBeNull();
	});

	it("hiding the page interrupts a recording of its own microphone", async () => {
		const r = useRecorder();
		await r.start();
		visibility = "hidden";
		document.dispatchEvent(new Event("visibilitychange"));
		await vi.waitFor(() => expect(r.interrupted.value).toBe("hidden"));
		await r.stop();
	});

	it("a caller's stream (live) is never interrupted", async () => {
		const shared = listenableStream();
		const r = useRecorder();
		await r.start(shared.stream);
		shared.track.dispatchEvent(new Event("mute"));
		visibility = "hidden";
		document.dispatchEvent(new Event("visibilitychange"));
		await Promise.resolve();
		expect(r.interrupted.value).toBeNull();
		expect(FakeMediaRecorder.instances[0]!.state).toBe("recording");
		await r.stop();
	});

	it("a microphone that cannot be reopened keeps the interruption with the reason", async () => {
		const first = listenableStream();
		getUserMedia.mockImplementationOnce(async () => first.stream);
		const r = useRecorder();
		await r.start();
		first.track.dispatchEvent(new Event("mute"));
		await vi.waitFor(() => expect(r.interrupted.value).toBe("muted"));

		getUserMedia.mockRejectedValueOnce(new DOMException("denied", "NotAllowedError"));
		expect(await r.continueRecording()).toBe(false);
		expect(r.interrupted.value).toBe("muted");
		expect(r.interruption.value).toContain("Mikrofonzugriff wurde verweigert.");
		await r.stop();
	});

	it("holds the wake lock from start to stop", async () => {
		const r = useRecorder();
		await r.start();
		expect(wake.acquire).toHaveBeenCalledTimes(1);
		expect(wake.release).not.toHaveBeenCalled();
		await r.stop();
		expect(wake.release).toHaveBeenCalled();
	});
});
