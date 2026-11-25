import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { useDeviceSpeech } from "~/composables/useDeviceSpeech";
import { useLiveCorrection } from "~/composables/useLiveCorrection";
import { decodePcm } from "~/utils/resonaSpeech";

// The Android app path: the ResonaSpeech plugin is compiled in and owns the microphone.
const { calls, plugin, service, emit, handles, transcribeSync } = vi.hoisted(() => {
	const calls: string[] = [];
	const listeners = new Map<string, Set<(event: unknown) => void>>();
	const handles: Array<{ remove: ReturnType<typeof vi.fn> }> = [];
	const plugin = {
		available: vi.fn(async (_opts: { language: string }) => ({ status: "available" })),
		install: vi.fn(async (_opts: { language: string }) => ({ started: true })),
		start: vi.fn(async (_opts: { language: string; emitPcm: boolean }) => {
			calls.push("plugin.start");
		}),
		stop: vi.fn(async () => {
			calls.push("plugin.stop");
			return { text: "" };
		}),
		addListener: vi.fn(async (name: string, cb: (event: unknown) => void) => {
			const set = listeners.get(name) ?? new Set();
			set.add(cb);
			listeners.set(name, set);
			const handle = { remove: vi.fn(async () => void set.delete(cb)) };
			handles.push(handle);
			return handle;
		}),
		removeAllListeners: vi.fn(async () => listeners.clear()),
	};
	const service = {
		requestPermissions: vi.fn(async () => ({})),
		createNotificationChannel: vi.fn(async () => {}),
		startForegroundService: vi.fn(async () => {
			calls.push("service.start");
		}),
		stopForegroundService: vi.fn(async () => {
			calls.push("service.stop");
		}),
	};
	function emit(name: string, event: unknown): void {
		for (const cb of [...(listeners.get(name) ?? [])]) cb(event);
	}
	return { calls, plugin, service, emit, handles, transcribeSync: vi.fn() };
});

vi.mock("@capacitor/core", () => ({
	Capacitor: {
		isNativePlatform: () => true,
		isPluginAvailable: (name: string) => name === "ResonaSpeech",
		getPlatform: () => "android",
	},
	registerPlugin: () => plugin,
	WebPlugin: class {},
}));
vi.mock("@capawesome-team/capacitor-android-foreground-service", () => ({
	ForegroundService: service,
	Importance: { Low: 2 },
	ServiceType: { Microphone: 128 },
}));
mockNuxtImport("useHub", () => () => ({ transcribeSync }));

/** `pcm` event payload for `samples`, encoded like the plugin (base64 little-endian int16). */
function pcmEvent(samples: Int16Array): { data: string } {
	const bytes = new Uint8Array(samples.buffer, samples.byteOffset, samples.byteLength);
	let binary = "";
	for (const b of bytes) binary += String.fromCharCode(b);
	return { data: btoa(binary) };
}

function seconds(s: number, value = 7): Int16Array {
	return new Int16Array(Math.round(s * 16000)).fill(value);
}

async function flush(): Promise<void> {
	for (let i = 0; i < 5; i += 1) await new Promise(r => setTimeout(r, 0));
}

describe("useDeviceSpeech (ResonaSpeech plugin)", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		calls.length = 0;
		handles.length = 0;
		plugin.stop.mockImplementation(async () => {
			calls.push("plugin.stop");
			return { text: "" };
		});
		plugin.start.mockImplementation(async () => {
			calls.push("plugin.start");
		});
	});

	it("is the native path and asks the plugin with the BCP 47 tag", async () => {
		const s = useDeviceSpeech();
		expect(s.native).toBe(true);
		plugin.available.mockResolvedValueOnce({ status: "downloadable" });
		expect(await s.availability("de")).toBe("downloadable");
		expect(plugin.available).toHaveBeenCalledWith({ language: "de-DE" });
		expect(await s.install("en")).toBe(true);
		expect(plugin.install).toHaveBeenCalledWith({ language: "en-US" });
	});

	it("starts the microphone foreground service before the plugin opens the microphone", async () => {
		const s = useDeviceSpeech();
		await s.start({ language: "de" });
		expect(calls).toEqual(["service.start", "plugin.start"]);
		expect(plugin.start).toHaveBeenCalledWith({ language: "de-DE", emitPcm: true });
		expect(s.status.value).toBe("OPEN");
		await s.finish();
	});

	it("shows committed and tentative text and reports each newly committed piece once", async () => {
		const s = useDeviceSpeech();
		const finals: string[] = [];
		s.onFinal(t => finals.push(t));
		await s.start({ language: "de" });

		emit("update", { committed: "", tentative: "Der Pat", revision: 1 });
		expect(s.partial.value).toBe("Der Pat");
		expect(s.final.value).toBe("");
		emit("update", { committed: "Der Patient kommt.", tentative: "", revision: 2 });
		emit("update", { committed: "Der Patient kommt.", tentative: "Heute", revision: 3 });
		emit("update", { committed: "Der Patient kommt. Heute geht es besser", tentative: "", revision: 4 });
		// A late, older event changes nothing.
		emit("update", { committed: "Der Patient kommt.", tentative: "Heu", revision: 3 });

		expect(s.final.value).toBe("Der Patient kommt. Heute geht es besser");
		expect(s.partial.value).toBe("");
		expect(s.fullText.value).toBe("Der Patient kommt. Heute geht es besser");
		expect(finals).toEqual(["Der Patient kommt.", "Heute geht es besser"]);
		await s.finish();
	});

	it("delivers the plugin's PCM to every frame listener and keeps it as WAV with keepAudio", async () => {
		const s = useDeviceSpeech();
		const frames: Int16Array[] = [];
		s.pcm.onFrame(f => frames.push(f));
		await s.start({ language: "de", keepAudio: true });

		const first = Int16Array.from([1, -1, 32767, -32768]);
		emit("pcm", pcmEvent(first));
		emit("pcm", pcmEvent(seconds(0.25)));
		expect(frames).toHaveLength(2);
		expect([...frames[0]!]).toEqual([1, -1, 32767, -32768]);
		expect(frames[1]!.length).toBe(4000);

		await s.finish();
		const rec = s.recording();
		expect(rec).not.toBeNull();
		expect(rec!.blob.type).toBe("audio/wav");
		expect(rec!.blob.size).toBe(44 + (4 + 4000) * 2);
		expect(rec!.seconds).toBeCloseTo(4004 / 16000);
	});

	it("keeps no audio without keepAudio", async () => {
		const s = useDeviceSpeech();
		await s.start({ language: "de" });
		emit("pcm", pcmEvent(seconds(1)));
		await s.finish();
		expect(s.recording()).toBeNull();
	});

	it("finish waits for the plugin, commits its trailing text and releases listeners and the service", async () => {
		const s = useDeviceSpeech();
		const finals: string[] = [];
		s.onFinal(t => finals.push(t));
		const frames: Int16Array[] = [];
		s.pcm.onFrame(f => frames.push(f));
		await s.start({ language: "de" });
		emit("update", { committed: "Anfang.", tentative: "unfertiges Ende", revision: 1 });

		plugin.stop.mockImplementationOnce(async () => {
			calls.push("plugin.stop");
			// The last frames arrive before stop() resolves.
			emit("pcm", pcmEvent(seconds(0.1)));
			return { text: "Anfang. unfertiges Ende" };
		});
		await s.finish();

		expect(s.status.value).toBe("CLOSED");
		expect(frames).toHaveLength(1);
		expect(s.final.value).toBe("Anfang. unfertiges Ende");
		expect(s.partial.value).toBe("");
		expect(finals).toEqual(["Anfang.", "unfertiges Ende"]);
		expect(calls).toEqual(["service.start", "plugin.start", "plugin.stop", "service.stop"]);
		expect(handles).toHaveLength(3);
		for (const h of handles) expect(h.remove).toHaveBeenCalled();

		// A straggler after the end changes nothing.
		emit("update", { committed: "Anfang. unfertiges Ende danach", tentative: "", revision: 9 });
		expect(s.final.value).toBe("Anfang. unfertiges Ende");
		// finish() twice stops the plugin once.
		await s.finish();
		expect(plugin.stop).toHaveBeenCalledTimes(1);
	});

	it("stop() ends the session without waiting", async () => {
		const s = useDeviceSpeech();
		await s.start({ language: "de" });
		s.stop();
		expect(s.status.value).toBe("CLOSED");
		await flush();
		expect(plugin.stop).toHaveBeenCalledTimes(1);
	});

	it("a preview failure shows the message and keeps the session; a microphone failure ends it", async () => {
		const s = useDeviceSpeech();
		await s.start({ language: "de" });
		emit("error", { code: "language-unavailable", message: "Das Sprachpaket fehlt.", fatal: false });
		expect(s.error.value).toBe("Sprachpaket für die lokale Erkennung fehlt");
		expect(s.status.value).toBe("OPEN");
		expect(plugin.stop).not.toHaveBeenCalled();

		emit("error", { code: "audio-capture", message: "Das Mikrofon lieferte keine Daten mehr.", fatal: true });
		await flush();
		expect(s.error.value).toBe("Kein Mikrofon");
		expect(s.status.value).toBe("CLOSED");
		expect(plugin.stop).toHaveBeenCalledTimes(1);
		expect(calls).toContain("service.stop");
	});

	it("a refused start throws the message and releases everything", async () => {
		plugin.start.mockRejectedValueOnce(Object.assign(new Error("Mikrofon nicht erlaubt"), { code: "not-allowed" }));
		const s = useDeviceSpeech();
		await expect(s.start({ language: "de" })).rejects.toThrow("Mikrofon nicht erlaubt");
		expect(s.status.value).toBe("CLOSED");
		expect(calls).toContain("service.stop");
		for (const h of handles) expect(h.remove).toHaveBeenCalled();
		// Events of the failed session are ignored.
		emit("update", { committed: "x", tentative: "", revision: 1 });
		expect(s.final.value).toBe("");
	});

	it("decodes the plugin's little-endian PCM", () => {
		expect([...decodePcm(pcmEvent(Int16Array.from([256, -2])).data)]).toEqual([256, -2]);
		// A stray odd byte is dropped, never a misaligned sample.
		expect([...decodePcm(btoa("\u0001\u0000\u0005"))]).toEqual([1]);
	});
});

describe("useLiveCorrection fed by the ResonaSpeech plugin", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		calls.length = 0;
		handles.length = 0;
		transcribeSync.mockReset();
		plugin.stop.mockImplementation(async () => ({ text: "" }));
		plugin.start.mockImplementation(async () => {});
	});

	it("takes the plugin's pcm events as its PcmCapture and re-transcribes the closed chunks", async () => {
		transcribeSync.mockResolvedValue("Vom Hub korrigiert.");
		const s = useDeviceSpeech();
		const c = useLiveCorrection();
		c.start({ capture: s.pcm, language: "de", engine: "", chunkSeconds: 2 });
		s.onFinal(t => c.addFinal(t));
		await s.start({ language: "de" });

		emit("pcm", pcmEvent(seconds(1, 3)));
		emit("update", { committed: "erster Teil", tentative: "", revision: 1 });
		expect(c.blocks.value).toHaveLength(1);
		emit("pcm", pcmEvent(seconds(1, 3)));
		emit("update", { committed: "erster Teil zweiter Teil", tentative: "", revision: 2 });
		// 2 s of plugin audio: the block closes and goes to the hub.
		expect(c.blocks.value).toHaveLength(2);
		expect(c.blocks.value[0]!.liveText).toBe("erster Teil zweiter Teil");

		emit("pcm", pcmEvent(seconds(0.75, 5)));
		plugin.stop.mockImplementationOnce(async () => {
			emit("pcm", pcmEvent(seconds(0.25, 5)));
			return { text: "erster Teil zweiter Teil dritter" };
		});
		// As the live page does: the plugin ends (last frames and trailing text in), then the correction.
		await s.finish();
		await c.finish();

		expect(transcribeSync).toHaveBeenCalledTimes(2);
		const [firstWav, name] = transcribeSync.mock.calls[0]!;
		expect(name).toBe("chunk.wav");
		expect((firstWav as Blob).size).toBe(44 + 2 * 16000 * 2);
		const [lastWav] = transcribeSync.mock.calls[1]!;
		// The frames sent while the plugin stopped belong to the last chunk.
		expect((lastWav as Blob).size).toBe(44 + 16000 * 2);
		expect(c.blocks.value.map(b => b.liveText)).toEqual(["erster Teil zweiter Teil", "dritter"]);
		expect(c.text.value).toBe("Vom Hub korrigiert. Vom Hub korrigiert.");
	});
});
