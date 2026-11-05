import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { usePcmCapture } from "~/composables/usePcmCapture";

interface FakeProcessor {
	onaudioprocess: ((e: unknown) => void) | null;
	connect: ReturnType<typeof vi.fn>;
	disconnect: ReturnType<typeof vi.fn>;
}

const created: { ctx: FakeContext[]; processor: FakeProcessor | null } = { ctx: [], processor: null };

class FakeContext {
	state = "running";
	sampleRate = 16000;
	destination = {};
	close = vi.fn(async () => {
		this.state = "closed";
	});

	resume = vi.fn(async () => {});
	constructor() {
		created.ctx.push(this);
	}

	createMediaStreamSource() {
		return { connect: vi.fn(), disconnect: vi.fn() };
	}

	createScriptProcessor() {
		created.processor = { onaudioprocess: null, connect: vi.fn(), disconnect: vi.fn() };
		return created.processor;
	}
}

function fakeStream() {
	const track = { stop: vi.fn() };
	return { track, stream: { getTracks: () => [track] } as unknown as MediaStream };
}

function emit(samples: number[]): void {
	created.processor!.onaudioprocess!({
		inputBuffer: { getChannelData: () => new Float32Array(samples) },
	});
}

describe("usePcmCapture", () => {
	beforeEach(() => {
		created.ctx = [];
		created.processor = null;
		(globalThis as Record<string, unknown>).AudioContext = FakeContext;
	});
	afterEach(() => {
		delete (globalThis as Record<string, unknown>).AudioContext;
	});

	it("delivers PCM16 frames to subscribers until they unsubscribe", async () => {
		const capture = usePcmCapture();
		expect(capture.sampleRate).toBe(16000);
		const a = vi.fn();
		const b = vi.fn();
		const offA = capture.onFrame(a);
		capture.onFrame(b);
		await capture.start(fakeStream().stream);

		emit([0, 1, -1]);
		expect(a).toHaveBeenCalledTimes(1);
		expect(Array.from(a.mock.calls[0]![0] as Int16Array)).toEqual([0, 32767, -32768]);
		expect(b).toHaveBeenCalledTimes(1);

		offA();
		emit([0.5]);
		expect(a).toHaveBeenCalledTimes(1);
		expect(b).toHaveBeenCalledTimes(2);
	});

	it("does not stop a caller-owned stream; stop() is idempotent", async () => {
		const { stream, track } = fakeStream();
		const capture = usePcmCapture();
		await capture.start(stream);

		capture.stop();
		capture.stop();
		expect(created.ctx[0]!.close).toHaveBeenCalledTimes(1);
		expect(created.processor!.onaudioprocess).toBeNull();
		expect(track.stop).not.toHaveBeenCalled();
	});

	it("stops its own mic stream on stop()", async () => {
		const { stream, track } = fakeStream();
		Object.defineProperty(globalThis.navigator, "mediaDevices", {
			value: { getUserMedia: vi.fn(async () => stream) },
			configurable: true,
		});
		const capture = usePcmCapture();
		await capture.start();
		capture.stop();
		expect(track.stop).toHaveBeenCalledTimes(1);
		Object.defineProperty(globalThis.navigator, "mediaDevices", { value: undefined, configurable: true });
	});

	it("stop() during a pending getUserMedia releases the mic without building a graph", async () => {
		const { stream, track } = fakeStream();
		const { promise, resolve } = Promise.withResolvers<MediaStream>();
		Object.defineProperty(globalThis.navigator, "mediaDevices", {
			value: { getUserMedia: vi.fn(() => promise) },
			configurable: true,
		});
		const capture = usePcmCapture();
		const pending = capture.start();
		capture.stop();
		resolve(stream);
		await pending;
		expect(track.stop).toHaveBeenCalledTimes(1);
		expect(created.ctx).toHaveLength(0);
		Object.defineProperty(globalThis.navigator, "mediaDevices", { value: undefined, configurable: true });
	});
});
