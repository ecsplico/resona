import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref, computed, reactive, type Ref } from "vue";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import LivePage from "~/pages/live.vue";

const live = vi.hoisted(() => ({
	partial: null as null | { value: string },
	final: null as null | { value: string },
	status: null as null | { value: string },
	error: null as null | { value: string | null },
}));
const liveStart = vi.hoisted(() => vi.fn(async () => {}));
const liveStop = vi.hoisted(() => vi.fn());
const finalListeners = vi.hoisted(() => new Set<(t: string) => void>());
const liveOnFinal = vi.hoisted(() => vi.fn((cb: (t: string) => void) => {
	finalListeners.add(cb);
	return () => {
		finalListeners.delete(cb);
	};
}));

mockNuxtImport("useLive", () => () => {
	live.partial ??= ref("");
	live.final ??= ref("");
	live.status ??= ref("CLOSED");
	live.error ??= ref<string | null>(null);
	const fullText = computed(() => {
		const f = live.final!.value;
		const p = live.partial!.value;
		return p ? `${f} ${p}`.trim() : f;
	});
	return {
		partial: live.partial,
		final: live.final,
		fullText,
		status: live.status,
		error: live.error,
		start: liveStart,
		stop: liveStop,
		onFinal: liveOnFinal,
	};
});

const device = vi.hoisted(() => ({
	partial: null as null | { value: string },
	final: null as null | { value: string },
	status: null as null | { value: string },
	error: null as null | { value: string | null },
	start: vi.fn(async () => {}),
	stop: vi.fn(),
}));
mockNuxtImport("useDeviceSpeech", () => () => {
	device.partial ??= ref("");
	device.final ??= ref("");
	device.status ??= ref("OPEN");
	device.error ??= ref<string | null>(null);
	return {
		partial: device.partial,
		final: device.final,
		fullText: computed(() => `${device.final!.value} ${device.partial!.value}`.trim()),
		status: device.status,
		error: device.error,
		start: device.start,
		stop: device.stop,
		onFinal: vi.fn(() => () => {}),
		availability: vi.fn(async () => "available"),
		install: vi.fn(async () => true),
	};
});

const settings = vi.hoisted(() => ({
	value: null as null | {
		source: "hub" | "device";
		hubEngine: string;
		correction: boolean;
		correctionEngine: string;
		chunkSeconds: number;
	},
}));
mockNuxtImport("useLiveSettings", () => () => {
	settings.value ??= reactive({
		source: "hub" as "hub" | "device",
		hubEngine: "",
		correction: false,
		correctionEngine: "",
		chunkSeconds: 15,
	});
	return settings.value;
});

const hubListEngines = vi.hoisted(() => vi.fn());
const listen = vi.hoisted(() => ({
	result: {
		engines: [] as { name: string; kind: string; private: boolean; models: string[] }[],
		default: null as string | null,
	},
}));
mockNuxtImport("useHub", () => () => ({
	token: () => "tok",
	listListenEngines: vi.fn(async () => listen.result),
	listEngines: hubListEngines,
}));

type TestBlock = {
	id: number;
	liveText: string;
	correctedText: string | null;
	state: "open" | "queued" | "correcting" | "corrected" | "failed";
	samples: Int16Array | null;
};
const corr = vi.hoisted(() => ({
	blocks: null as null | { value: TestBlock[] },
	finishResolve: null as null | (() => void),
	start: vi.fn(),
	addFinal: vi.fn(),
	reset: vi.fn(),
	finish: vi.fn(),
}));
mockNuxtImport("useLiveCorrection", () => () => {
	corr.blocks ??= ref<TestBlock[]>([]);
	const blocks = corr.blocks as Ref<TestBlock[]>;
	return {
		blocks,
		text: computed(() => blocks.value.map(b => b.correctedText ?? b.liveText).join(" ").trim()),
		pending: computed(() => blocks.value.filter(b => b.state === "queued" || b.state === "correcting").length),
		start: corr.start,
		addFinal: corr.addFinal,
		finish: corr.finish,
		reset: corr.reset,
	};
});

const capture = vi.hoisted(() => ({
	sampleRate: 16000,
	start: vi.fn(async () => {}),
	stop: vi.fn(),
	onFrame: vi.fn(() => () => {}),
}));
mockNuxtImport("usePcmCapture", () => () => capture);

async function flush(): Promise<void> {
	for (let i = 0; i < 5; i += 1) {
		await new Promise(r => setTimeout(r, 0));
	}
}

async function mountLive() {
	const w = await mountSuspended(LivePage);
	await flush();
	await w.vm.$nextTick();
	return w;
}

async function stopAndSave(w: Awaited<ReturnType<typeof mountLive>>): Promise<void> {
	await w.find("[data-testid=\"stop-live\"]").trigger("click");
	await flush();
	await w.find("[data-testid=\"confirm-save\"]").trigger("click");
	await flush();
}

function setLive(key: "partial" | "final" | "status", value: string): void {
	live[key]!.value = value;
}
const partial = {
	set value(v: string) {
		setLive("partial", v);
	},
};
const final = {
	set value(v: string) {
		setLive("final", v);
	},
};
const status = {
	set value(v: string) {
		setLive("status", v);
	},
};

const recorderStart = vi.hoisted(() => vi.fn(async () => {}));
const recorderStop = vi.hoisted(() => vi.fn(async () => new Blob(["x"], { type: "audio/webm" })));

mockNuxtImport("useRecorder", () => () => ({
	recording: ref(false),
	paused: ref(false),
	elapsed: ref(7),
	levels: ref([] as number[]),
	start: recorderStart,
	stop: recorderStop,
	pause: vi.fn(),
	resume: vi.fn(),
}));

const store = vi.hoisted(() => ({
	createRecording: vi.fn(),
	createTranscript: vi.fn(),
}));
mockNuxtImport("useDataStore", () => () => store);

const navigateTo = vi.hoisted(() => vi.fn());
mockNuxtImport("navigateTo", () => navigateTo);
const nav = vi.hoisted(() => ({ back: vi.fn(async () => {}), leaveTo: vi.fn(async () => {}) }));
mockNuxtImport("useBackNav", () => () => nav);

describe("live transcription page", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		live.partial ??= ref("");
		live.final ??= ref("");
		live.status ??= ref("CLOSED");
		partial.value = "";
		final.value = "";
		status.value = "OPEN";
		live.error ??= ref<string | null>(null);
		live.error.value = null;
		device.partial ??= ref("");
		device.final ??= ref("");
		device.status ??= ref("OPEN");
		device.error ??= ref<string | null>(null);
		device.final.value = "";
		device.partial.value = "";
		device.error.value = null;
		settings.value ??= reactive({
			source: "hub" as "hub" | "device",
			hubEngine: "",
			correction: false,
			correctionEngine: "",
			chunkSeconds: 15,
		});
		settings.value.source = "hub";
		settings.value.hubEngine = "";
		settings.value.correction = false;
		settings.value.correctionEngine = "";
		settings.value.chunkSeconds = 15;
		finalListeners.clear();
		corr.blocks ??= ref<TestBlock[]>([]);
		corr.blocks.value = [];
		corr.finishResolve = null;
		corr.finish.mockImplementation(() => new Promise<void>((resolve) => {
			corr.finishResolve = resolve;
		}));
		hubListEngines.mockResolvedValue({ engines: [], default: "whisper-large" });
		listen.result = {
			engines: [{ name: "whisper-live", kind: "local", private: true, models: [] }],
			default: "whisper-live",
		};
		store.createRecording.mockReset();
		store.createTranscript.mockReset();
		store.createRecording.mockResolvedValue({ id: "rec-1" });
		store.createTranscript.mockResolvedValue({ id: "t-1" });
	});

	it("auto-starts the live session on mount", async () => {
		await mountLive();
		expect(liveStart).toHaveBeenCalled();
	});

	it("offers a way back to the recordings", async () => {
		const w = await mountLive();
		const back = w.find("[data-testid=\"live-back\"]");
		expect(back.text()).toBe("Zurück");
		await back.trigger("click");
		expect(nav.back).toHaveBeenCalledWith("/");
		expect(navigateTo).not.toHaveBeenCalled();
	});

	it("shows the rolling transcript with final and partial text", async () => {
		const w = await mountLive();
		final.value = "Der Patient";
		partial.value = "klagt über";
		await w.vm.$nextTick();
		const transcript = w.find("[data-testid=\"transcript\"]");
		expect(transcript.exists()).toBe(true);
		expect(transcript.text()).toContain("Der Patient");
		expect(transcript.text()).toContain("klagt über");
	});

	it("copies the rolling transcript to the clipboard", async () => {
		const writeText = vi.fn(async () => {});
		Object.defineProperty(globalThis.navigator, "clipboard", {
			value: { writeText },
			configurable: true,
		});

		const w = await mountLive();
		// No copy affordance until there is text to copy.
		expect(w.find("[data-testid=\"copy-live\"]").exists()).toBe(false);

		final.value = "Der Patient";
		partial.value = "klagt über Kopfschmerzen";
		await w.vm.$nextTick();

		const copyBtn = w.find("[data-testid=\"copy-live\"]");
		expect(copyBtn.exists()).toBe(true);
		await copyBtn.trigger("click");

		expect(writeText).toHaveBeenCalledWith("Der Patient klagt über Kopfschmerzen");
	});

	it("shows a reconnect banner only when CLOSED mid-session", async () => {
		const w = await mountLive();
		expect(w.find("[data-testid=\"reconnect-banner\"]").exists()).toBe(false);
		status.value = "CLOSED";
		await w.vm.$nextTick();
		expect(w.find("[data-testid=\"reconnect-banner\"]").exists()).toBe(true);
	});

	it("reveals a title prompt on stop and stops live + recorder", async () => {
		const w = await mountLive();
		expect(w.find("[data-testid=\"title-input\"]").exists()).toBe(false);
		await w.find("[data-testid=\"stop-live\"]").trigger("click");
		await w.vm.$nextTick();
		expect(recorderStop).toHaveBeenCalled();
		expect(liveStop).toHaveBeenCalled();
		expect(w.find("[data-testid=\"title-input\"]").exists()).toBe(true);
	});

	it("confirming creates the recording then its transcript through the data store, and navigates home", async () => {
		final.value = "Der Befund ist unauffällig.";
		const w = await mountLive();
		await w.find("[data-testid=\"stop-live\"]").trigger("click");
		await w.vm.$nextTick();

		const input = w.find("[data-testid=\"title-input\"]").find("input");
		await input.setValue("Visite Station 3");
		await w.find("[data-testid=\"confirm-save\"]").trigger("click");
		await w.vm.$nextTick();
		await new Promise(r => setTimeout(r, 0));
		await w.vm.$nextTick();

		// The recorder mock yields a non-empty blob, which is passed through as the audio.
		expect(store.createRecording).toHaveBeenCalledWith(
			expect.objectContaining({
				title: "Visite Station 3",
				source: "live",
				status: "done",
			}),
			expect.any(Blob),
			"Visite Station 3.webm",
		);

		expect(store.createTranscript).toHaveBeenCalledWith("rec-1", { text: "Der Befund ist unauffällig.", engine: "whisper-live" });

		// recording created before transcript
		expect(store.createRecording.mock.invocationCallOrder[0]!)
			.toBeLessThan(store.createTranscript.mock.invocationCallOrder[0]!);

		expect(nav.leaveTo).toHaveBeenCalledWith("/");
	});

	it("surfaces an error and does not navigate when saving fails", async () => {
		store.createRecording.mockReset();
		store.createRecording.mockRejectedValueOnce(new Error("network down"));

		final.value = "Der Befund ist unauffällig.";
		const w = await mountLive();
		await w.find("[data-testid=\"stop-live\"]").trigger("click");
		await w.vm.$nextTick();

		const input = w.find("[data-testid=\"title-input\"]").find("input");
		await input.setValue("Visite Station 3");
		await w.find("[data-testid=\"confirm-save\"]").trigger("click");
		await w.vm.$nextTick();
		await new Promise(r => setTimeout(r, 0));
		await w.vm.$nextTick();

		expect(nav.leaveTo).not.toHaveBeenCalled();
		const err = w.find("[data-testid=\"live-save-error\"]");
		expect(err.exists()).toBe(true);
		expect(err.text()).toContain("Speichern fehlgeschlagen");
	});

	it("hub source starts the hub stream with the default engine and private unset", async () => {
		await mountLive();
		expect(device.start).not.toHaveBeenCalled();
		expect(liveStart).toHaveBeenCalledWith(expect.objectContaining({
			language: "de",
			engine: undefined,
			private: undefined,
			capture,
		}));
	});

	it("a selected cloud listen engine is started with private=false", async () => {
		settings.value!.hubEngine = "deepgram";
		listen.result = {
			engines: [
				{ name: "whisper-live", kind: "local", private: true, models: [] },
				{ name: "deepgram", kind: "cloud", private: false, models: [] },
			],
			default: "whisper-live",
		};
		await mountLive();
		expect(liveStart).toHaveBeenCalledWith(expect.objectContaining({ engine: "deepgram", private: false }));
	});

	it("device source uses on-device speech and stamps web-speech on save", async () => {
		settings.value!.source = "device";
		const w = await mountLive();
		expect(liveStart).not.toHaveBeenCalled();
		expect(device.start).toHaveBeenCalledWith({ language: "de" });

		device.final!.value = "Lokal erkannt";
		await w.vm.$nextTick();
		expect(w.find("[data-testid=\"transcript\"]").text()).toContain("Lokal erkannt");

		await stopAndSave(w);
		expect(device.stop).toHaveBeenCalled();
		expect(store.createTranscript).toHaveBeenCalledWith("rec-1", { text: "Lokal erkannt", engine: "web-speech" });
	});

	it("device source is allowed even when the hub offers no live streaming", async () => {
		settings.value!.source = "device";
		listen.result = { engines: [], default: null };
		const w = await mountLive();
		expect(w.find("[data-testid=\"live-no-hub-stream\"]").exists()).toBe(false);
		expect(device.start).toHaveBeenCalled();
	});

	it("hub source with no listen engines shows the notice and does not start", async () => {
		listen.result = { engines: [], default: null };
		const w = await mountLive();
		expect(liveStart).not.toHaveBeenCalled();
		const notice = w.find("[data-testid=\"live-no-hub-stream\"]");
		expect(notice.exists()).toBe(true);
		expect(notice.text()).toContain("Dieser Hub bietet kein Live-Streaming an");
		const startBtn = w.find("[data-testid=\"start-live\"]");
		expect(startBtn.exists()).toBe(true);
		expect(startBtn.attributes("disabled")).toBeDefined();
	});

	it("stamps the selected hub engine, else the listen default, else hub-live", async () => {
		settings.value!.hubEngine = "deepgram";
		final.value = "a";
		let w = await mountLive();
		await stopAndSave(w);
		expect(store.createTranscript).toHaveBeenLastCalledWith("rec-1", { text: "a", engine: "deepgram" });
		w.unmount();

		settings.value!.hubEngine = "";
		listen.result = { engines: [{ name: "other", kind: "local", private: true, models: [] }], default: null };
		w = await mountLive();
		await stopAndSave(w);
		expect(store.createTranscript).toHaveBeenLastCalledWith("rec-1", { text: "a", engine: "hub-live" });
	});

	it("shows the source error as a banner instead of the reconnect notice", async () => {
		const w = await mountLive();
		live.error!.value = "engine down";
		status.value = "CLOSED";
		await w.vm.$nextTick();
		const banner = w.find("[data-testid=\"live-source-error\"]");
		expect(banner.exists()).toBe(true);
		expect(banner.text()).toContain("engine down");
		expect(w.find("[data-testid=\"reconnect-banner\"]").exists()).toBe(false);
	});

	describe("with live correction", () => {
		beforeEach(() => {
			settings.value!.correction = true;
			settings.value!.correctionEngine = "";
			settings.value!.chunkSeconds = 20;
		});

		function setBlocks(blocks: Partial<TestBlock>[]): void {
			corr.blocks!.value = blocks.map((b, i) => ({
				id: i + 1,
				liveText: "",
				correctedText: null,
				state: "open",
				samples: null,
				...b,
			}));
		}

		it("starts correction with the capture and feeds source finals into it; unsubscribes on stop", async () => {
			settings.value!.correctionEngine = "whisper-large";
			const w = await mountLive();
			expect(corr.start).toHaveBeenCalledWith({ capture, language: "de", engine: "whisper-large", chunkSeconds: 20 });
			expect(liveOnFinal).toHaveBeenCalledTimes(1);
			for (const cb of finalListeners) {
				cb("Hallo Welt");
			}
			expect(corr.addFinal).toHaveBeenCalledWith("Hallo Welt");

			await w.find("[data-testid=\"stop-live\"]").trigger("click");
			await flush();
			expect(finalListeners.size).toBe(0);
			expect(corr.finish.mock.invocationCallOrder[0]!).toBeLessThan(capture.stop.mock.invocationCallOrder[0]!);
		});

		it("feeds the unfinished partial into correction before finishing on stop", async () => {
			const w = await mountLive();
			partial.value = "  letzte Worte ";
			await w.find("[data-testid=\"stop-live\"]").trigger("click");
			await flush();
			expect(corr.addFinal).toHaveBeenCalledWith("letzte Worte");
			expect(corr.addFinal.mock.invocationCallOrder.at(-1)!).toBeLessThan(corr.finish.mock.invocationCallOrder[0]!);
		});

		it("renders blocks with corrected/pending classes followed by the partial", async () => {
			const w = await mountLive();
			setBlocks([
				{ liveText: "eins roh", correctedText: "Eins.", state: "corrected" },
				{ liveText: "zwei", state: "correcting" },
				{ liveText: "drei", state: "queued" },
				{ liveText: "vier", state: "failed" },
				{ liveText: "fünf", state: "open" },
			]);
			partial.value = "sechs";
			await w.vm.$nextTick();

			const blocks = w.findAll("[data-testid=\"live-block\"]");
			expect(blocks.map(b => b.text())).toEqual(["Eins.", "zwei", "drei", "vier", "fünf"]);
			expect(blocks[0]!.classes()).toContain("live__block--corrected");
			expect(blocks[0]!.classes()).not.toContain("live__block--pending");
			expect(blocks[1]!.classes()).toContain("live__block--pending");
			expect(blocks[2]!.classes()).toContain("live__block--pending");
			for (const b of [blocks[3]!, blocks[4]!]) {
				expect(b.classes()).not.toContain("live__block--pending");
				expect(b.classes()).not.toContain("live__block--corrected");
			}
			const text = w.find("[data-testid=\"transcript\"]").text();
			expect(text).not.toContain("eins roh");
			expect(text.endsWith("sechs")).toBe(true);
		});

		it("save waits for finish, then saves the correction text with the combined engine stamp", async () => {
			final.value = "eins roh zwei";
			const w = await mountLive();
			setBlocks([
				{ liveText: "eins roh", correctedText: "Eins.", state: "corrected" },
				{ liveText: "zwei", state: "queued" },
			]);
			await w.find("[data-testid=\"stop-live\"]").trigger("click");
			await flush();

			const notice = w.find("[data-testid=\"live-correction-pending\"]");
			expect(notice.exists()).toBe(true);
			expect(notice.text()).toBe("Korrektur läuft… (1 Abschnitte)");

			await w.find("[data-testid=\"confirm-save\"]").trigger("click");
			await flush();
			expect(store.createRecording).not.toHaveBeenCalled();
			expect(store.createTranscript).not.toHaveBeenCalled();

			setBlocks([
				{ liveText: "eins roh", correctedText: "Eins.", state: "corrected" },
				{ liveText: "zwei", correctedText: "Zwei.", state: "corrected" },
			]);
			corr.finishResolve!();
			await flush();

			expect(w.find("[data-testid=\"live-correction-pending\"]").exists()).toBe(false);
			expect(store.createTranscript).toHaveBeenCalledWith("rec-1", { text: "Eins. Zwei.", engine: "whisper-live+whisper-large" });
			expect(nav.leaveTo).toHaveBeenCalledWith("/");
		});

		it("stamps the configured correction engine, and falls back to hub when engines fail to load", async () => {
			settings.value!.correctionEngine = "openai";
			let w = await mountLive();
			setBlocks([{ liveText: "a", correctedText: "A.", state: "corrected" }]);
			corr.finish.mockResolvedValueOnce(undefined);
			await stopAndSave(w);
			expect(store.createTranscript).toHaveBeenLastCalledWith("rec-1", { text: "A.", engine: "whisper-live+openai" });
			w.unmount();

			settings.value!.correctionEngine = "";
			hubListEngines.mockRejectedValueOnce(new Error("down"));
			w = await mountLive();
			setBlocks([{ liveText: "a", correctedText: "A.", state: "corrected" }]);
			corr.finish.mockResolvedValueOnce(undefined);
			await stopAndSave(w);
			expect(store.createTranscript).toHaveBeenLastCalledWith("rec-1", { text: "A.", engine: "whisper-live+hub" });
		});

		it("keeps the plain live stamp when no block got corrected", async () => {
			const w = await mountLive();
			setBlocks([{ liveText: "nur live", state: "failed" }]);
			corr.finish.mockResolvedValueOnce(undefined);
			await stopAndSave(w);
			expect(store.createTranscript).toHaveBeenCalledWith("rec-1", { text: "nur live", engine: "whisper-live" });
		});
	});

	it("without correction it neither starts correction nor loads hub engines", async () => {
		final.value = "plain";
		const w = await mountLive();
		expect(corr.start).not.toHaveBeenCalled();
		expect(liveOnFinal).not.toHaveBeenCalled();
		expect(hubListEngines).not.toHaveBeenCalled();
		expect(w.find("[data-testid=\"live-block\"]").exists()).toBe(false);
		await stopAndSave(w);
		expect(corr.finish).not.toHaveBeenCalled();
		expect(store.createTranscript).toHaveBeenCalledWith("rec-1", { text: "plain", engine: "whisper-live" });
	});
});
