import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref, computed, reactive } from "vue";
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
		onFinal: vi.fn(() => () => {}),
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

const listen = vi.hoisted(() => ({
	result: {
		engines: [] as { name: string; kind: string; private: boolean; models: string[] }[],
		default: null as string | null,
	},
}));
mockNuxtImport("useHub", () => () => ({
	token: () => "tok",
	listListenEngines: vi.fn(async () => listen.result),
}));

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

		expect(navigateTo).toHaveBeenCalledWith("/");
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

		expect(navigateTo).not.toHaveBeenCalled();
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
});
