import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref, nextTick } from "vue";
import type { Ref } from "vue";
import { useLive } from "~/composables/useLive";
import { useHubSettings } from "~/composables/useHubSettings";

// Controllable useWebSocket spy. The mutable refs are populated by the mock
// factory (which runs once @vueuse/core is first imported, by which point the
// top-level `ref` binding is available) and shared with the test body.
const ws = vi.hoisted(() => ({
	data: null as Ref<string | null> | null,
	status: null as Ref<string> | null,
	send: vi.fn(),
	close: vi.fn(),
	open: vi.fn(),
	lastUrl: "" as string,
	lastOpts: undefined as unknown,
}));

vi.mock("@vueuse/core", () => {
	ws.data = ref<string | null>(null);
	ws.status = ref<string>("CONNECTING");
	return {
		useWebSocket: vi.fn((url: string, opts: unknown) => {
			ws.lastUrl = url;
			ws.lastOpts = opts;
			return { data: ws.data, status: ws.status, send: ws.send, close: ws.close, open: ws.open };
		}),
	};
});

function resultsFrame(transcript: string, isFinal: boolean): string {
	return JSON.stringify({
		type: "Results",
		channel: { alternatives: [{ transcript }] },
		is_final: isFinal,
	});
}

describe("useLive", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		ws.data!.value = null;
		ws.status!.value = "CONNECTING";
		// no AudioContext / mediaDevices in happy-dom — the audio path must be skipped
		delete (globalThis as Record<string, unknown>).AudioContext;
		delete (globalThis as Record<string, unknown>).webkitAudioContext;
	});

	it("start() opens the WS at /v1/listen with all required query params", () => {
		useHubSettings().token = "tok";
		const live = useLive();
		void live.start({ language: "en", engine: "deepgram" });

		const url = new URL(ws.lastUrl);
		expect(url.pathname).toBe("/v1/listen");
		expect(url.protocol).toBe("ws:");
		expect(url.searchParams.get("encoding")).toBe("linear16");
		expect(url.searchParams.get("sample_rate")).toBe("16000");
		expect(url.searchParams.get("interim_results")).toBe("true");
		expect(url.searchParams.get("language")).toBe("en");
		expect(url.searchParams.get("engine")).toBe("deepgram");
		expect(url.searchParams.get("token")).toBe("tok");
	});

	it("defaults language from runtime config and omits engine when not provided", () => {
		const live = useLive();
		void live.start();

		const url = new URL(ws.lastUrl);
		expect(url.searchParams.get("language")).toBe("de");
		expect(url.searchParams.has("engine")).toBe(false);
	});

	it("reconnects up to 3 times with a 2 s delay", () => {
		const live = useLive();
		void live.start();
		expect(ws.lastOpts).toEqual({ autoReconnect: { retries: 3, delay: 2000 } });
	});

	it("omits engine when empty and private unless explicitly false", () => {
		const live = useLive();
		void live.start({ engine: "", private: true });
		let url = new URL(ws.lastUrl);
		expect(url.searchParams.has("engine")).toBe(false);
		expect(url.searchParams.has("private")).toBe(false);

		void live.start({ engine: "deepgram", private: false });
		url = new URL(ws.lastUrl);
		expect(url.searchParams.get("engine")).toBe("deepgram");
		expect(url.searchParams.get("private")).toBe("false");
	});

	it("streams capture frames while running and unsubscribes on stop", () => {
		let frameCb: ((pcm: Int16Array) => void) | null = null;
		const unsubscribe = vi.fn(() => {
			frameCb = null;
		});
		const capture = {
			sampleRate: 16000,
			start: vi.fn(async () => {}),
			stop: vi.fn(),
			onFrame: vi.fn((cb: (pcm: Int16Array) => void) => {
				frameCb = cb;
				return unsubscribe;
			}),
		};
		const live = useLive();
		void live.start({ capture });

		const pcm = new Int16Array([1, 2, 3]);
		frameCb!(pcm);
		expect(ws.send).toHaveBeenCalledWith(pcm.buffer);

		live.stop();
		expect(unsubscribe).toHaveBeenCalledTimes(1);
		// the capture itself is owned by the caller
		expect(capture.stop).not.toHaveBeenCalled();
	});

	it("an Error frame sets error and stops the stream", async () => {
		const live = useLive();
		void live.start();

		ws.data!.value = JSON.stringify({ type: "Error", description: "engine down" });
		await nextTick();

		expect(live.error.value).toBe("engine down");
		expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: "CloseStream" }));
		expect(ws.close).toHaveBeenCalled();

		// a later frame is no longer processed
		ws.data!.value = resultsFrame("zu spät", true);
		await nextTick();
		expect(live.final.value).toBe("");

		// a new session clears the error
		void live.start();
		expect(live.error.value).toBeNull();
	});

	it("onFinal fires with each final transcript only, and unsubscribes", async () => {
		const live = useLive();
		const seen: string[] = [];
		const off = live.onFinal(t => seen.push(t));
		void live.start();

		ws.data!.value = resultsFrame("teil", false);
		await nextTick();
		ws.data!.value = resultsFrame("ganzer satz", true);
		await nextTick();
		expect(seen).toEqual(["ganzer satz"]);

		off();
		ws.data!.value = resultsFrame("noch einer", true);
		await nextTick();
		expect(seen).toEqual(["ganzer satz"]);
	});

	it("updates partial on a non-final Results frame", async () => {
		const live = useLive();
		void live.start();

		ws.data!.value = resultsFrame("hallo", false);
		await nextTick();

		expect(live.partial.value).toBe("hallo");
		expect(live.final.value).toBe("");
	});

	it("appends to final and clears partial on a final Results frame", async () => {
		const live = useLive();
		void live.start();

		ws.data!.value = resultsFrame("erster satz", true);
		await nextTick();

		expect(live.final.value).toBe("erster satz");
		expect(live.partial.value).toBe("");

		ws.data!.value = resultsFrame("zweiter satz", true);
		await nextTick();

		expect(live.final.value).toBe("erster satz zweiter satz");
		expect(live.partial.value).toBe("");
	});

	it("fullText combines final and partial", async () => {
		const live = useLive();
		void live.start();

		ws.data!.value = resultsFrame("fertig", true);
		await nextTick();
		ws.data!.value = resultsFrame("noch", false);
		await nextTick();

		expect(live.fullText.value).toBe("fertig noch");
	});

	it("ignores non-Results frames and non-JSON data", async () => {
		const live = useLive();
		void live.start();

		ws.data!.value = JSON.stringify({ type: "Metadata", duration: 1 });
		await nextTick();
		ws.data!.value = "not json";
		await nextTick();

		expect(live.partial.value).toBe("");
		expect(live.final.value).toBe("");
	});

	it("stop() sends CloseStream then closes", () => {
		const live = useLive();
		void live.start();
		live.stop();

		expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: "CloseStream" }));
		expect(ws.close).toHaveBeenCalled();
		const sendOrder = ws.send.mock.invocationCallOrder[0]!;
		const closeOrder = ws.close.mock.invocationCallOrder[0]!;
		expect(sendOrder).toBeLessThan(closeOrder);
	});

	it("exposes status from the WS", () => {
		const live = useLive();
		void live.start();
		ws.status!.value = "OPEN";
		expect(live.status.value).toBe("OPEN");
	});
});
