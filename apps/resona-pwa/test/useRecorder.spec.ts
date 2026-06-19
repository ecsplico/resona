import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { effectScope } from "vue";
import { useRecorder } from "~/composables/useRecorder";

// A minimal fake MediaRecorder that mirrors the subset of the API the
// composable relies on. happy-dom implements none of it.
class FakeMediaRecorder {
	static instances: FakeMediaRecorder[] = [];

	state: "inactive" | "recording" | "paused" = "inactive";
	stream: MediaStream;
	ondataavailable: ((e: { data: Blob }) => void) | null = null;
	onstop: (() => void) | null = null;

	constructor(stream: MediaStream) {
		this.stream = stream;
		FakeMediaRecorder.instances.push(this);
	}

	start(): void {
		this.state = "recording";
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
		expect(blob.type).toBe("audio/webm");
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
});
