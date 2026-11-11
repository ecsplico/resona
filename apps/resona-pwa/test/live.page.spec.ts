import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ref, computed, reactive, type Ref } from "vue";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import LivePage from "~/pages/live.vue";
import { HubError } from "~/composables/useHub";
import { DEVICE_SPEECH_HINTS } from "~/composables/useDeviceSpeech";

const unfinished = vi.hoisted(() => ({ discardUnfinished: vi.fn(async (_id: string) => {}) }));
vi.mock("~/data/unfinishedRecordings", () => unfinished);

const live = vi.hoisted(() => ({
	partial: null as null | { value: string },
	final: null as null | { value: string },
	status: null as null | { value: string },
	error: null as null | { value: string | null },
	closeInfo: null as null | { value: { code: number; reason: string } | null },
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
	live.closeInfo ??= ref<{ code: number; reason: string } | null>(null);
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
		closeInfo: live.closeInfo,
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
	availability: vi.fn(async (): Promise<string> => "available"),
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
		availability: device.availability,
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
	error: null as unknown,
	hasToken: true,
}));
const listListenEngines = vi.hoisted(() => vi.fn(async () => {
	if (listen.error) {
		throw listen.error;
	}
	return listen.result;
}));
mockNuxtImport("useHub", () => () => ({
	token: () => "tok",
	hasToken: () => listen.hasToken,
	listListenEngines,
	listEngines: hubListEngines,
}));

const route = vi.hoisted(() => ({ query: {} as Record<string, string> }));
mockNuxtImport("useRoute", () => () => route);

const suggestTitleRemote = vi.hoisted(() => vi.fn(async (_text: string, _opts?: { language?: string; signal?: AbortSignal }): Promise<string | null> => null));
mockNuxtImport("useTitleJob", () => () => ({ suggestTitleRemote }));

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
const recorderContinue = vi.hoisted(() => vi.fn(async () => true));
const recorderInterrupted = ref<string | null>(null);
const recorderInterruption = ref<string | null>(null);

mockNuxtImport("useWebRecorder", () => () => ({
	recording: ref(false),
	paused: ref(false),
	elapsed: ref(7),
	levels: ref([] as number[]),
	interrupted: recorderInterrupted,
	interruption: recorderInterruption,
	start: recorderStart,
	stop: recorderStop,
	pause: vi.fn(),
	resume: vi.fn(),
	continueRecording: recorderContinue,
	sessionId: ref<string | null>("live-1"),
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
		recorderInterrupted.value = null;
		recorderInterruption.value = null;
		live.error ??= ref<string | null>(null);
		live.error.value = null;
		live.closeInfo ??= ref<{ code: number; reason: string } | null>(null);
		live.closeInfo.value = null;
		device.partial ??= ref("");
		device.final ??= ref("");
		device.status ??= ref("OPEN");
		device.error ??= ref<string | null>(null);
		device.final.value = "";
		device.partial.value = "";
		device.error.value = null;
		device.availability.mockResolvedValue("available");
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
		listen.error = null;
		listen.hasToken = true;
		route.query = {};
		suggestTitleRemote.mockResolvedValue(null);
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
		// Saved for good: the IndexedDB copy of the live audio goes.
		expect(unfinished.discardUnfinished).toHaveBeenCalledWith("live-1");
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
		// Not saved: the copy stays for the next open of the list.
		expect(unfinished.discardUnfinished).not.toHaveBeenCalled();
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
		expect(w.find("[data-testid=\"live-notice\"]").exists()).toBe(false);
		expect(device.start).toHaveBeenCalled();
	});

	function notice(w: Awaited<ReturnType<typeof mountLive>>) {
		return w.find("[data-testid=\"live-notice\"]");
	}
	function startButton(w: Awaited<ReturnType<typeof mountLive>>) {
		return w.find("[data-testid=\"start-live\"]");
	}

	it("hub reachable but without live engine: says so and does not start", async () => {
		listen.result = { engines: [], default: null };
		const w = await mountLive();
		expect(liveStart).not.toHaveBeenCalled();
		expect(notice(w).attributes("data-kind")).toBe("no-engine");
		expect(notice(w).text()).toContain("Der Hub hat keinen Live-Erkenner");
		expect(startButton(w).attributes("disabled")).toBeDefined();
	});

	it("no hub token on the device: points to Einstellungen → Verbindung without calling the hub", async () => {
		listen.hasToken = false;
		const w = await mountLive();
		expect(listListenEngines).not.toHaveBeenCalled();
		expect(liveStart).not.toHaveBeenCalled();
		expect(notice(w).attributes("data-kind")).toBe("no-token");
		expect(notice(w).text()).toContain("kein Hub-Token");
		expect(notice(w).text()).toContain("Einstellungen → Verbindung");
		expect(startButton(w).attributes("disabled")).toBeDefined();
	});

	it.each([401, 403])("token rejected by the hub (%i): says the token is invalid or revoked", async (code) => {
		listen.error = new HubError(code, "unauthorized", "invalid credential");
		const w = await mountLive();
		expect(liveStart).not.toHaveBeenCalled();
		expect(notice(w).attributes("data-kind")).toBe("unauthorized");
		expect(notice(w).text()).toContain("ungültig oder widerrufen");
		expect(notice(w).text()).not.toContain("Live-Erkenner");
		expect(startButton(w).attributes("disabled")).toBeDefined();
	});

	it("unreachable hub: reports the network, and start re-checks before streaming", async () => {
		listen.error = new HubError(0, "network", "Failed to fetch");
		const w = await mountLive();
		expect(liveStart).not.toHaveBeenCalled();
		expect(notice(w).attributes("data-kind")).toBe("unreachable");
		expect(notice(w).text()).toContain("nicht erreichbar");
		expect(startButton(w).attributes("disabled")).toBeUndefined();

		// still down: nothing starts
		await startButton(w).trigger("click");
		await flush();
		expect(liveStart).not.toHaveBeenCalled();

		listen.error = null;
		await startButton(w).trigger("click");
		await flush();
		expect(liveStart).toHaveBeenCalled();
		expect(notice(w).exists()).toBe(false);
	});

	it("other hub errors show the status instead of an empty engine list", async () => {
		listen.error = new HubError(500, "http_500", "Internal Server Error");
		const w = await mountLive();
		expect(notice(w).attributes("data-kind")).toBe("error");
		expect(notice(w).text()).toContain("HTTP 500");
	});

	it.each(["unsupported", "unavailable"] as const)("device source %s: shows the unsupported hint and disables start", async (state) => {
		settings.value!.source = "device";
		device.availability.mockResolvedValue(state);
		const w = await mountLive();
		expect(device.start).not.toHaveBeenCalled();
		expect(notice(w).attributes("data-kind")).toBe("device-unsupported");
		expect(notice(w).text()).toContain(DEVICE_SPEECH_HINTS[state]);
		expect(startButton(w).attributes("disabled")).toBeDefined();
		await startButton(w).trigger("click");
		expect(device.start).not.toHaveBeenCalled();
	});

	it("device source with a downloadable pack: hints at the install, waits for the user", async () => {
		settings.value!.source = "device";
		device.availability.mockResolvedValue("downloadable");
		const w = await mountLive();
		expect(device.start).not.toHaveBeenCalled();
		expect(notice(w).attributes("data-kind")).toBe("device-downloadable");
		expect(notice(w).text()).toContain(DEVICE_SPEECH_HINTS.downloadable);
		expect(startButton(w).attributes("disabled")).toBeUndefined();
	});

	it("names the WebSocket close code and reason while reconnecting", async () => {
		const w = await mountLive();
		live.closeInfo!.value = { code: 1011, reason: "engine down" };
		status.value = "CLOSED";
		await w.vm.$nextTick();
		const banner = w.find("[data-testid=\"reconnect-banner\"]");
		expect(banner.text()).toContain("Code 1011: engine down");
	});

	describe("title suggestion", () => {
		// Enough final words (LIVE_SUGGESTION_MIN_WORDS) for the suggestion to show.
		const dictation = "Also äh der Patient klagt über starke Kopfschmerzen seit drei Tagen Punkt Er nimmt seit gestern zweimal täglich Ibuprofen und trinkt viel Wasser, Fieber hat er keines Punkt";

		it("shows a suggestion during recording once enough final text is there", async () => {
			const w = await mountLive();
			final.value = "Der Patient";
			await w.vm.$nextTick();
			expect(w.find("[data-testid=\"live-title-suggestion\"]").exists()).toBe(false);

			final.value = dictation;
			await w.vm.$nextTick();
			expect(w.find("[data-testid=\"live-title-suggestion\"]").text()).toContain("Patient klagt über starke Kopfschmerzen");
		});

		it("shows no suggestion for a short dictation yet pre-fills its rule title on stop", async () => {
			const w = await mountLive();
			final.value = "Der Patient klagt über starke Kopfschmerzen seit drei Tagen.";
			await w.vm.$nextTick();
			expect(w.find("[data-testid=\"live-title-suggestion\"]").exists()).toBe(false);
			await w.find("[data-testid=\"stop-live\"]").trigger("click");
			await flush();
			expect((w.find("[data-testid=\"title-input\"]").find("input").element as HTMLInputElement).value)
				.toBe("Patient klagt über starke Kopfschmerzen");
		});

		it("pre-fills the save dialog with exactly that suggestion and saves an edit", async () => {
			const w = await mountLive();
			final.value = dictation;
			partial.value = "und Übelkeit";
			await w.vm.$nextTick();
			await w.find("[data-testid=\"stop-live\"]").trigger("click");
			await flush();
			const input = w.find("[data-testid=\"title-input\"]").find("input");
			expect((input.element as HTMLInputElement).value).toBe("Patient klagt über starke Kopfschmerzen");

			await input.setValue("Kopfschmerz-Anamnese");
			await w.find("[data-testid=\"confirm-save\"]").trigger("click");
			await flush();
			expect(store.createRecording).toHaveBeenCalledWith(
				expect.objectContaining({ title: "Kopfschmerz-Anamnese" }),
				expect.any(Blob),
				"Kopfschmerz-Anamnese.webm",
			);
		});

		it("falls back to date and time when nothing usable was recognised", async () => {
			const w = await mountLive();
			final.value = "äh ähm";
			await w.find("[data-testid=\"stop-live\"]").trigger("click");
			await flush();
			const value = (w.find("[data-testid=\"title-input\"]").find("input").element as HTMLInputElement).value;
			expect(value).toMatch(/^Diktat \d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$/);
		});

		it("saves a cleared title under the suggestion, or date and time without usable text", async () => {
			for (const [text, expected] of [
				[dictation, /^Patient klagt über starke Kopfschmerzen$/],
				["äh ähm", /^Diktat \d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$/],
			] as const) {
				store.createRecording.mockClear();
				const w = await mountLive();
				final.value = text;
				await w.find("[data-testid=\"stop-live\"]").trigger("click");
				await flush();
				await w.find("[data-testid=\"title-input\"]").find("input").setValue("  ");
				await w.find("[data-testid=\"confirm-save\"]").trigger("click");
				await flush();
				const [input, , filename] = store.createRecording.mock.calls[0]!;
				expect(input.title).toMatch(expected);
				expect(filename).toBe(`${input.title}.webm`);
				w.unmount();
			}
		});

		it("keeps a calendar title and shows no competing suggestion", async () => {
			route.query = { title: "Visite Station 3 – 10.11.2026" };
			const w = await mountLive();
			final.value = dictation;
			await w.vm.$nextTick();
			expect(w.find("[data-testid=\"live-title-suggestion\"]").exists()).toBe(false);
			await w.find("[data-testid=\"stop-live\"]").trigger("click");
			await flush();
			expect((w.find("[data-testid=\"title-input\"]").find("input").element as HTMLInputElement).value)
				.toBe("Visite Station 3 – 10.11.2026");
			expect(suggestTitleRemote).not.toHaveBeenCalled();
		});

		function titleValue(w: Awaited<ReturnType<typeof mountLive>>): string {
			return (w.find("[data-testid=\"title-input\"]").find("input").element as HTMLInputElement).value;
		}

		function deferredRemote(): { resolve: (v: string | null) => void; signal: () => AbortSignal } {
			const { promise, resolve } = Promise.withResolvers<string | null>();
			suggestTitleRemote.mockImplementation(() => promise);
			return {
				resolve,
				signal: () => suggestTitleRemote.mock.calls[0]![1]!.signal!,
			};
		}

		it("a remote title replaces the rule suggestion after stop", async () => {
			const remote = deferredRemote();
			const w = await mountLive();
			final.value = dictation;
			await w.find("[data-testid=\"stop-live\"]").trigger("click");
			await flush();
			expect(suggestTitleRemote).toHaveBeenCalledWith(dictation, expect.objectContaining({ language: "de" }));
			expect(titleValue(w)).toBe("Patient klagt über starke Kopfschmerzen");
			expect(w.find("[data-testid=\"live-title-refining\"]").exists()).toBe(true);

			remote.resolve("Kopfschmerzen seit drei Tagen");
			await flush();
			expect(titleValue(w)).toBe("Kopfschmerzen seit drei Tagen");
			expect(w.find("[data-testid=\"live-title-suggestion\"]").text()).toContain("Kopfschmerzen seit drei Tagen");
			expect(w.find("[data-testid=\"live-title-refining\"]").exists()).toBe(false);
		});

		it("a typed title aborts the refinement and is not replaced", async () => {
			const remote = deferredRemote();
			const w = await mountLive();
			final.value = dictation;
			await w.find("[data-testid=\"stop-live\"]").trigger("click");
			await flush();
			await w.find("[data-testid=\"title-input\"]").find("input").setValue("Mein Titel");
			expect(remote.signal().aborted).toBe(true);

			remote.resolve("Kopfschmerzen seit drei Tagen");
			await flush();
			expect(titleValue(w)).toBe("Mein Titel");
		});

		it("null from the hub keeps the rule title silently", async () => {
			const w = await mountLive();
			final.value = dictation;
			await w.find("[data-testid=\"stop-live\"]").trigger("click");
			await flush();
			expect(suggestTitleRemote).toHaveBeenCalled();
			expect(titleValue(w)).toBe("Patient klagt über starke Kopfschmerzen");
			expect(w.find("[data-testid=\"live-title-refining\"]").exists()).toBe(false);
		});

		it("is aborted on unmount and not started for short text or without token", async () => {
			const remote = deferredRemote();
			let w = await mountLive();
			final.value = dictation;
			await w.find("[data-testid=\"stop-live\"]").trigger("click");
			await flush();
			w.unmount();
			expect(remote.signal().aborted).toBe(true);

			suggestTitleRemote.mockClear();
			w = await mountLive();
			final.value = "Kurze Notiz";
			await w.find("[data-testid=\"stop-live\"]").trigger("click");
			await flush();
			expect(suggestTitleRemote).not.toHaveBeenCalled();
			w.unmount();

			listen.hasToken = false;
			settings.value!.source = "device";
			w = await mountLive();
			device.final!.value = dictation;
			await w.find("[data-testid=\"stop-live\"]").trigger("click");
			await flush();
			expect(suggestTitleRemote).not.toHaveBeenCalled();
		});

		describe("on-device refinement (browser language model)", () => {
			// Over the 40-word on-device threshold.
			const LONG40 = `${dictation} Die Beweglichkeit des Nackens ist frei, neurologisch zeigen sich keine Auffälligkeiten. Wir empfehlen weiter Ibuprofen bei Bedarf und sehen ihn in einer Woche zur Kontrolle wieder.`;
			const RULE = "Patient klagt über starke Kopfschmerzen";
			const REFINED = "Kopfschmerzen seit drei Tagen";

			function stubModel(state: string, answer: () => Promise<string> = async () => REFINED) {
				const session = { prompt: vi.fn((_input: string, _opts: { signal: AbortSignal }) => answer()), destroy: vi.fn() };
				const model = { availability: vi.fn(async () => state), create: vi.fn(async () => session) };
				vi.stubGlobal("LanguageModel", model);
				return { model, session };
			}

			function pendingModel() {
				const answer = Promise.withResolvers<string>();
				return { answer, ...stubModel("available", () => answer.promise) };
			}

			function shown(w: Awaited<ReturnType<typeof mountLive>>): string {
				return w.find("[data-testid=\"live-title-suggestion\"]").text().replace("Titelvorschlag: ", "");
			}

			afterEach(() => {
				vi.unstubAllGlobals();
			});

			it("without the model (the normal test case) the rule suggestion stays and the hub may refine", async () => {
				const w = await mountLive();
				final.value = LONG40;
				await flush();
				expect(shown(w)).toBe(RULE);
				await w.find("[data-testid=\"stop-live\"]").trigger("click");
				await flush();
				expect(titleValue(w)).toBe(RULE);
				expect(suggestTitleRemote).toHaveBeenCalledTimes(1);
			});

			it.each(["downloadable", "downloading", "unavailable"])("a %s model is never created or downloaded; behaviour stays as without", async (state) => {
				const { model } = stubModel(state);
				const w = await mountLive();
				final.value = LONG40;
				await flush();
				expect(shown(w)).toBe(RULE);
				await w.find("[data-testid=\"stop-live\"]").trigger("click");
				await flush();
				expect(titleValue(w)).toBe(RULE);
				expect(model.availability).toHaveBeenCalledTimes(1);
				expect(model.create).not.toHaveBeenCalled();
				expect(suggestTitleRemote).toHaveBeenCalledTimes(1);
			});

			it("an available model replaces the suggestion during recording, once, and the hub is not asked", async () => {
				const { model, session } = stubModel("available");
				const w = await mountLive();
				final.value = dictation;
				await flush();
				// The rule suggestion shows first; the refinement waits for more text.
				expect(shown(w)).toBe(RULE);
				expect(model.create).not.toHaveBeenCalled();

				final.value = LONG40;
				await flush();
				expect(shown(w)).toBe(REFINED);
				expect(session.prompt.mock.calls[0]![0]).toContain(LONG40);

				final.value = `${LONG40} Verband morgen.`;
				await flush();
				await w.find("[data-testid=\"stop-live\"]").trigger("click");
				await flush();
				expect(session.prompt).toHaveBeenCalledTimes(1);
				expect(suggestTitleRemote).not.toHaveBeenCalled();
				expect(titleValue(w)).toBe(REFINED);

				await w.find("[data-testid=\"confirm-save\"]").trigger("click");
				await flush();
				expect(store.createRecording.mock.calls[0]![0].title).toBe(REFINED);
			});

			it("a shorter recording is refined once after stop and the untouched field takes the result", async () => {
				const { answer, session } = pendingModel();
				const w = await mountLive();
				final.value = dictation;
				await flush();
				expect(session.prompt).not.toHaveBeenCalled();

				await w.find("[data-testid=\"stop-live\"]").trigger("click");
				await flush();
				expect(titleValue(w)).toBe(RULE);
				expect(session.prompt).toHaveBeenCalledTimes(1);

				answer.resolve(REFINED);
				await flush();
				expect(titleValue(w)).toBe(REFINED);
				expect(suggestTitleRemote).not.toHaveBeenCalled();
			});

			it("typing in the title aborts the refinement and drops a late answer", async () => {
				const { answer, session } = pendingModel();
				const w = await mountLive();
				final.value = dictation;
				await w.find("[data-testid=\"stop-live\"]").trigger("click");
				await flush();

				await w.find("[data-testid=\"title-input\"]").find("input").setValue("Mein Titel");
				expect(session.prompt.mock.calls[0]![1].signal.aborted).toBe(true);
				answer.resolve(REFINED);
				await flush();
				expect(titleValue(w)).toBe("Mein Titel");
				expect(suggestTitleRemote).not.toHaveBeenCalled();
			});

			it("saving aborts the refinement; the shown suggestion is saved", async () => {
				const { answer, session } = pendingModel();
				const w = await mountLive();
				final.value = dictation;
				await stopAndSave(w);
				expect(session.prompt.mock.calls[0]![1].signal.aborted).toBe(true);
				answer.resolve(REFINED);
				await flush();
				expect(store.createRecording.mock.calls[0]![0].title).toBe(RULE);
			});

			it("discarding aborts the refinement", async () => {
				const { session } = pendingModel();
				const w = await mountLive();
				final.value = dictation;
				await w.find("[data-testid=\"stop-live\"]").trigger("click");
				await flush();
				await w.find("[data-testid=\"cancel-save\"]").trigger("click");
				expect(session.prompt.mock.calls[0]![1].signal.aborted).toBe(true);
			});

			it.each([
				["empty", ""],
				["too long", "Kontrolle der Wundheilung nach Implantation einer Knietotalendoprothese rechts bei Arthrose"],
				["English", "Severe headache for three days"],
			])("an unusable answer (%s) keeps the rule suggestion; the hub may still refine", async (_name, reply) => {
				stubModel("available", async () => reply);
				const w = await mountLive();
				final.value = LONG40;
				await flush();
				expect(shown(w)).toBe(RULE);
				await w.find("[data-testid=\"stop-live\"]").trigger("click");
				await flush();
				expect(titleValue(w)).toBe(RULE);
				expect(suggestTitleRemote).toHaveBeenCalledTimes(1);
			});

			it("transcript and audio recording keep running while the model works", async () => {
				const stream = { getTracks: () => [] } as unknown as MediaStream;
				Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia: vi.fn(async () => stream) } });
				try {
					const { answer, session } = pendingModel();
					const w = await mountLive();
					expect(recorderStart).toHaveBeenCalledWith(stream);
					final.value = LONG40;
					await flush();
					expect(session.prompt).toHaveBeenCalledTimes(1);

					// Model still working: new text renders, nothing was stopped.
					partial.value = "Weiter im Text";
					final.value = `${LONG40} Verband morgen.`;
					await flush();
					expect(w.find("[data-testid=\"transcript\"]").text()).toContain("Verband morgen. Weiter im Text");
					expect(recorderStop).not.toHaveBeenCalled();
					expect(capture.stop).not.toHaveBeenCalled();
					expect(liveStop).not.toHaveBeenCalled();

					answer.resolve(REFINED);
					await flush();
					expect(shown(w)).toBe(REFINED);
					expect(recorderStop).not.toHaveBeenCalled();
				}
				finally {
					Reflect.deleteProperty(navigator, "mediaDevices");
				}
			});
		});
	});

	it("an interrupted recording pauses transcription with the audio and offers Fortsetzen", async () => {
		const track = { stop: vi.fn() };
		const stream = { getTracks: () => [track] } as unknown as MediaStream;
		Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia: vi.fn(async () => stream) } });
		try {
			const w = await mountLive();
			expect(w.find("[data-testid=\"live-interrupted\"]").exists()).toBe(false);

			recorderInterrupted.value = "hidden";
			recorderInterruption.value = "Die App war im Hintergrund.";
			await flush();
			const notice = w.find("[data-testid=\"live-interrupted\"]");
			expect(notice.text()).toContain("Aufnahme unterbrochen");
			expect(notice.text()).toContain("Die App war im Hintergrund.");
			// No text without audio: transcription and the microphone stop with the recording.
			expect(capture.stop).toHaveBeenCalled();
			expect(track.stop).toHaveBeenCalled();

			await w.find("[data-testid=\"live-continue\"]").trigger("click");
			expect(recorderContinue).toHaveBeenCalledTimes(1);
		}
		finally {
			Reflect.deleteProperty(navigator, "mediaDevices");
		}
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

	it("a typed title is not overwritten when the correction finishes with other text", async () => {
		settings.value!.correction = true;
		const w = await mountLive();
		corr.blocks!.value = [{ id: 1, liveText: "Der Patient klagt über starke Kopfschmerzen seit Tagen. Er nimmt seit gestern zweimal täglich Ibuprofen und trinkt viel Wasser, Fieber hat er keines.", correctedText: null, state: "queued", samples: null }];
		await w.find("[data-testid=\"stop-live\"]").trigger("click");
		await flush();
		const input = w.find("[data-testid=\"title-input\"]").find("input");
		expect((input.element as HTMLInputElement).value).toBe("Patient klagt über starke Kopfschmerzen");
		await input.setValue("Mein Titel");

		corr.blocks!.value = [{ id: 1, liveText: "x", correctedText: "Die Patientin berichtet über anhaltenden Schwindel heute früh.", state: "corrected", samples: null }];
		corr.finishResolve!();
		await flush();
		expect((input.element as HTMLInputElement).value).toBe("Mein Titel");

		await w.find("[data-testid=\"confirm-save\"]").trigger("click");
		await flush();
		expect(store.createRecording).toHaveBeenCalledWith(expect.objectContaining({ title: "Mein Titel" }), expect.any(Blob), "Mein Titel.webm");
	});

	it("a finishing correction never changes the suggestion shown at stop", async () => {
		settings.value!.correction = true;
		const w = await mountLive();
		corr.blocks!.value = [{ id: 1, liveText: "Der Patient klagt über starke Kopfschmerzen seit Tagen. Er nimmt seit gestern zweimal täglich Ibuprofen und trinkt viel Wasser, Fieber hat er keines.", correctedText: null, state: "queued", samples: null }];
		await w.vm.$nextTick();
		expect(w.find("[data-testid=\"live-title-suggestion\"]").text()).toBe("Titelvorschlag: Patient klagt über starke Kopfschmerzen");
		await w.find("[data-testid=\"stop-live\"]").trigger("click");
		await flush();
		const input = w.find("[data-testid=\"title-input\"]").find("input");
		expect((input.element as HTMLInputElement).value).toBe("Patient klagt über starke Kopfschmerzen");

		corr.blocks!.value = [{ id: 1, liveText: "x", correctedText: "Die Patientin berichtet über anhaltenden Schwindel heute früh. Sie hat seit dem Morgen Übelkeit, kein Erbrechen, keine Kopfschmerzen und keine Sehstörungen, Blutdruck stabil.", state: "corrected", samples: null }];
		corr.finishResolve!();
		await flush();
		expect((input.element as HTMLInputElement).value).toBe("Patient klagt über starke Kopfschmerzen");
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
