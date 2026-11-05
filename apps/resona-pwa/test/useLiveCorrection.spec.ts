import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import type { PcmCapture } from "~/composables/usePcmCapture";
import { useLiveCorrection } from "~/composables/useLiveCorrection";

const { transcribeSync } = vi.hoisted(() => ({ transcribeSync: vi.fn() }));

mockNuxtImport("useHub", () => () => ({ transcribeSync }));

function fakeCapture() {
	const listeners = new Set<(pcm: Int16Array) => void>();
	const capture: PcmCapture = {
		sampleRate: 16000,
		start: vi.fn(async () => {}),
		stop: vi.fn(),
		onFrame(cb) {
			listeners.add(cb);
			return () => listeners.delete(cb);
		},
	} as unknown as PcmCapture;
	return {
		capture,
		listeners,
		emitSeconds(seconds: number) {
			const pcm = new Int16Array(Math.round(seconds * 16000));
			for (const l of listeners) l(pcm);
		},
	};
}

async function flush() {
	for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

describe("useLiveCorrection", () => {
	beforeEach(() => {
		transcribeSync.mockReset();
	});
	afterEach(() => {
		vi.useRealTimers();
	});

	function setup(chunkSeconds = 10) {
		const cap = fakeCapture();
		const c = useLiveCorrection();
		c.start({ capture: cap.capture, language: "de", engine: "", chunkSeconds });
		return { c, cap };
	}

	it("keeps the block open below the threshold and closes it at chunkSeconds", async () => {
		transcribeSync.mockResolvedValue("korrigiert");
		const { c, cap } = setup(10);
		cap.emitSeconds(9);
		c.addFinal("hallo");
		expect(c.blocks.value).toHaveLength(1);
		expect(c.blocks.value[0]!.state).toBe("open");
		cap.emitSeconds(1);
		c.addFinal("welt");
		expect(c.blocks.value).toHaveLength(2);
		expect(c.blocks.value[0]!.liveText).toBe("hallo welt");
		expect(c.blocks.value[1]!.state).toBe("open");
		expect(transcribeSync).toHaveBeenCalledTimes(1);
		const [blob, name, fields] = transcribeSync.mock.calls[0]!;
		expect(name).toBe("chunk.wav");
		expect(fields).toEqual({ language: "de", engine: undefined });
		expect((blob as Blob).type).toBe("audio/wav");
		expect((blob as Blob).size).toBe(44 + 10 * 16000 * 2);
	});

	it("does not close a block on frames alone, only on addFinal", () => {
		const { c, cap } = setup(10);
		cap.emitSeconds(20);
		expect(c.blocks.value).toHaveLength(1);
		expect(transcribeSync).not.toHaveBeenCalled();
	});

	it("replaces live text with corrected text and clears samples", async () => {
		transcribeSync.mockResolvedValue("  Hallo Welt.  ");
		const { c, cap } = setup(10);
		cap.emitSeconds(10);
		c.addFinal("hallo welt");
		expect(c.pending.value).toBe(1);
		await flush();
		const b = c.blocks.value[0]!;
		expect(b.state).toBe("corrected");
		expect(b.correctedText).toBe("Hallo Welt.");
		expect(b.samples).toBeNull();
		expect(c.pending.value).toBe(0);
		expect(c.text.value).toBe("Hallo Welt.");
	});

	it("passes a non-empty engine through", async () => {
		transcribeSync.mockResolvedValue("x");
		const cap = fakeCapture();
		const c = useLiveCorrection();
		c.start({ capture: cap.capture, language: "en", engine: "whisper", chunkSeconds: 8 });
		cap.emitSeconds(8);
		c.addFinal("x");
		expect(transcribeSync.mock.calls[0]![2]).toEqual({ language: "en", engine: "whisper" });
	});

	it("keeps live text on error and on empty result", async () => {
		transcribeSync.mockRejectedValueOnce(new Error("504")).mockResolvedValueOnce("   ");
		const { c, cap } = setup(10);
		cap.emitSeconds(10);
		c.addFinal("eins");
		cap.emitSeconds(10);
		c.addFinal("zwei");
		await flush();
		expect(c.blocks.value[0]!.state).toBe("failed");
		expect(c.blocks.value[0]!.correctedText).toBeNull();
		expect(c.blocks.value[0]!.samples).toBeNull();
		expect(c.blocks.value[1]!.state).toBe("failed");
		expect(c.text.value).toBe("eins zwei");
	});

	it("fails a block shorter than 0.5 s without a request", async () => {
		const { c, cap } = setup(10);
		cap.emitSeconds(0.4);
		c.addFinal("kurz");
		await c.finish();
		expect(transcribeSync).not.toHaveBeenCalled();
		expect(c.blocks.value).toHaveLength(1);
		expect(c.blocks.value[0]!.state).toBe("failed");
		expect(c.text.value).toBe("kurz");
	});

	it("corrects sequentially and lands results in their own blocks", async () => {
		const first = Promise.withResolvers<string>();
		const second = Promise.withResolvers<string>();
		transcribeSync.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
		const { c, cap } = setup(10);
		cap.emitSeconds(10);
		c.addFinal("a");
		cap.emitSeconds(10);
		c.addFinal("b");
		await flush();
		expect(transcribeSync).toHaveBeenCalledTimes(1);
		expect(c.blocks.value[0]!.state).toBe("correcting");
		expect(c.blocks.value[1]!.state).toBe("queued");
		expect(c.pending.value).toBe(2);

		second.resolve("B"); // not yet requested; must not affect anything
		first.resolve("A");
		await flush();
		expect(transcribeSync).toHaveBeenCalledTimes(2);
		await flush();
		expect(c.blocks.value[0]!.correctedText).toBe("A");
		expect(c.blocks.value[1]!.correctedText).toBe("B");
		expect(c.text.value).toBe("A B");
	});

	it("joins without double spaces when blocks are empty", async () => {
		transcribeSync.mockResolvedValue("");
		const { c, cap } = setup(10);
		cap.emitSeconds(10);
		c.addFinal("eins");
		cap.emitSeconds(10);
		c.addFinal("");
		cap.emitSeconds(10);
		c.addFinal("drei");
		await c.finish();
		expect(c.text.value).toBe("eins drei");
	});

	it("finish closes the open block, unsubscribes and resolves when drained", async () => {
		const d = Promise.withResolvers<string>();
		transcribeSync.mockReturnValueOnce(d.promise);
		const { c, cap } = setup(10);
		cap.emitSeconds(2);
		c.addFinal("rest");
		let done = false;
		const p = c.finish().then(() => {
			done = true;
		});
		expect(cap.listeners.size).toBe(0);
		expect(transcribeSync).toHaveBeenCalledTimes(1);
		await flush();
		expect(done).toBe(false);
		d.resolve("Rest.");
		await p;
		expect(c.blocks.value).toHaveLength(1);
		expect(c.text.value).toBe("Rest.");
	});

	it("finish drops an empty open block", async () => {
		const { c } = setup(10);
		await c.finish();
		expect(c.blocks.value).toHaveLength(0);
		expect(transcribeSync).not.toHaveBeenCalled();
	});

	it("finish resolves after 90 s when the queue does not drain", async () => {
		vi.useFakeTimers();
		transcribeSync.mockReturnValue(new Promise<string>(() => {}));
		const { c, cap } = setup(10);
		cap.emitSeconds(5);
		c.addFinal("hängt");
		let done = false;
		void c.finish().then(() => {
			done = true;
		});
		await vi.advanceTimersByTimeAsync(89_000);
		expect(done).toBe(false);
		await vi.advanceTimersByTimeAsync(1_000);
		expect(done).toBe(true);
	});

	it("reset unsubscribes and clears blocks", () => {
		const { c, cap } = setup(10);
		cap.emitSeconds(1);
		c.addFinal("x");
		c.reset();
		expect(cap.listeners.size).toBe(0);
		expect(c.blocks.value).toHaveLength(0);
		expect(c.text.value).toBe("");
	});
});
