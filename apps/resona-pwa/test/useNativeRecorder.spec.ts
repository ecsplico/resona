import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useNativeRecorder } from "~/composables/useNativeRecorder";

// One ordered log of native calls: the foreground service must be up before the
// recorder starts (Android cuts the mic of a locked phone otherwise).
const { calls, recorder, service, filesystem, silence, events, platform } = vi.hoisted(() => {
	const calls: string[] = [];
	const log = <T>(name: string, value?: T) => vi.fn(async () => {
		calls.push(name);
		return value as T;
	});
	// Registered native event listeners by event name; `remove()` drops them again.
	const events = new Map<string, (event: never) => void>();
	const addListener = vi.fn(async (name: string, listener: (event: never) => void) => {
		events.set(name, listener);
		return { remove: vi.fn(async () => void events.delete(name)) };
	});
	return {
		calls,
		events,
		platform: { name: "android" },
		recorder: {
			requestPermissions: log("requestPermissions", { recordAudio: "granted" }),
			startRecording: log("startRecording"),
			pauseRecording: log("pauseRecording"),
			resumeRecording: log("resumeRecording"),
			stopRecording: log("stopRecording", { uri: "file:///cache/rec.aac", duration: 0 }),
			cancelRecording: log("cancelRecording"),
			getCurrentAmplitude: vi.fn(async () => ({ value: 0.5 })),
			addListener,
		},
		silence: {
			start: vi.fn(async () => ({ supported: true })),
			stop: vi.fn(async () => undefined),
			addListener,
		},
		service: {
			requestPermissions: log("notificationPermission"),
			createNotificationChannel: log("createChannel"),
			startForegroundService: log("startService"),
			stopForegroundService: log("stopService"),
		},
		filesystem: { deleteFile: log("deleteFile") },
	};
});

vi.mock("@capacitor/core", () => ({
	Capacitor: {
		isNativePlatform: () => true,
		isPluginAvailable: () => true,
		getPlatform: () => platform.name,
		convertFileSrc: (uri: string) => uri.replace("file://", "https://localhost/_capacitor_file_"),
	},
	registerPlugin: () => silence,
}));
vi.mock("@capgo/capacitor-audio-recorder", () => ({ CapacitorAudioRecorder: recorder }));
vi.mock("@capawesome-team/capacitor-android-foreground-service", () => ({
	ForegroundService: service,
	Importance: { Low: 2 },
	ServiceType: { Microphone: 128 },
}));
vi.mock("@capacitor/filesystem", () => ({ Filesystem: filesystem }));

const fetchMock = vi.fn(async () => new Response("aac-bytes"));

describe("useNativeRecorder", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		calls.length = 0;
		events.clear();
		platform.name = "android";
		vi.useFakeTimers();
		vi.stubGlobal("fetch", fetchMock);
		recorder.requestPermissions.mockImplementation(async () => {
			calls.push("requestPermissions");
			return { recordAudio: "granted" };
		});
		recorder.startRecording.mockImplementation(async () => {
			calls.push("startRecording");
		});
		service.startForegroundService.mockImplementation(async () => {
			calls.push("startService");
		});
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
	});

	it("records inside the microphone service and returns the ADTS file as audio/aac", async () => {
		const r = useNativeRecorder();
		expect(await r.start()).toBe(true);
		expect(r.recording.value).toBe(true);

		const blob = await r.stop();

		expect(calls.indexOf("startService")).toBeLessThan(calls.indexOf("startRecording"));
		expect(calls.indexOf("stopRecording")).toBeLessThan(calls.indexOf("stopService"));
		expect(service.startForegroundService).toHaveBeenCalledWith(expect.objectContaining({ serviceType: 128 }));
		expect(fetchMock).toHaveBeenCalledWith("https://localhost/_capacitor_file_/cache/rec.aac");
		expect(blob.type).toBe("audio/aac");
		expect(recorder.startRecording).toHaveBeenCalledWith({ format: "aac" });
		expect(await blob.text()).toBe("aac-bytes");
		// Not a recorder file the recordings list would offer: deleted at once, no session.
		expect(filesystem.deleteFile).toHaveBeenCalledWith({ path: "file:///cache/rec.aac" });
		expect(r.sessionId.value).toBeNull();
		expect(r.recording.value).toBe(false);
	});

	it("keeps the recorder's file as the session until the caller saves or discards it (an app killed meanwhile finds it)", async () => {
		const name = "recording-20261113-235935-79f42f22-a966-46ec-8f15-29e750b85c48.aac";
		recorder.stopRecording.mockResolvedValueOnce({ uri: `file:///data/cache/capacitor-audio-recorder/${name}`, duration: 0 });
		const r = useNativeRecorder();
		await r.start();

		expect(await (await r.stop()).text()).toBe("aac-bytes");
		expect(filesystem.deleteFile).not.toHaveBeenCalled();
		expect(r.sessionId.value).toBe(`native:${name}`);

		await r.start();
		expect(r.sessionId.value).toBeNull();
		await r.stop();
	});

	it("elapsed follows the wall clock, without pauses, even when timers stalled (locked phone)", async () => {
		const r = useNativeRecorder();
		await r.start();

		// Locked for 10 minutes: no interval ran in the web view meanwhile.
		vi.setSystemTime(Date.now() + 600_000);
		await vi.advanceTimersByTimeAsync(1000);
		expect(r.elapsed.value).toBe(601);

		r.pause();
		vi.setSystemTime(Date.now() + 30_000);
		await vi.advanceTimersByTimeAsync(1000);
		expect(r.elapsed.value).toBe(601);

		r.resume();
		await vi.advanceTimersByTimeAsync(2000);
		expect(r.elapsed.value).toBe(603);
		await r.stop();
	});

	it("a stop() while the native recorder starts cancels that recording", async () => {
		const { promise: pending, resolve: release } = Promise.withResolvers<undefined>();
		recorder.startRecording.mockImplementation(() => {
			calls.push("startRecording");
			return pending;
		});
		const r = useNativeRecorder();
		const started = r.start();
		await vi.waitFor(() => expect(recorder.startRecording).toHaveBeenCalled());

		const blob = await r.stop();
		release(undefined);

		expect(await started).toBe(false);
		expect(blob.size).toBe(0);
		expect(recorder.cancelRecording).toHaveBeenCalled();
		expect(service.stopForegroundService).toHaveBeenCalled();
		expect(r.recording.value).toBe(false);
	});

	it("a restart while the stale start is still pending keeps the new recording", async () => {
		const { promise: pending, resolve: release } = Promise.withResolvers<undefined>();
		recorder.startRecording.mockImplementationOnce(() => {
			calls.push("startRecording");
			return pending;
		});
		const r = useNativeRecorder();
		const first = r.start();
		await vi.waitFor(() => expect(recorder.startRecording).toHaveBeenCalled());
		await r.stop();
		const second = r.start();
		release(undefined);

		expect(await first).toBe(false);
		expect(await second).toBe(true);
		expect(r.recording.value).toBe(true);
		// The stale recording and its service are gone before the new ones start.
		expect(calls.slice(calls.indexOf("cancelRecording"))).toEqual([
			"cancelRecording",
			"stopService",
			"notificationPermission",
			"createChannel",
			"startService",
			"startRecording",
		]);
		await r.stop();
	});

	it("refuses with a German message when the microphone is denied", async () => {
		recorder.requestPermissions.mockImplementation(async () => ({ recordAudio: "denied" }));
		const r = useNativeRecorder();

		await expect(r.start()).rejects.toThrow("Mikrofonzugriff wurde verweigert.");
		expect(service.startForegroundService).not.toHaveBeenCalled();
		expect(recorder.startRecording).not.toHaveBeenCalled();
	});

	it("does not record when the foreground service cannot start", async () => {
		service.startForegroundService.mockImplementation(async () => {
			throw new Error("ForegroundServiceStartNotAllowedException");
		});
		const r = useNativeRecorder();

		await expect(r.start()).rejects.toThrow("Hintergrundaufnahme konnte nicht gestartet werden.");
		expect(recorder.startRecording).not.toHaveBeenCalled();
		expect(r.recording.value).toBe(false);
	});

	it("keeps the file when it cannot be read back", async () => {
		fetchMock.mockImplementationOnce(async () => {
			throw new TypeError("Failed to fetch");
		});
		const r = useNativeRecorder();
		await r.start();

		const blob = await r.stop();

		expect(blob.size).toBe(0);
		expect(filesystem.deleteFile).not.toHaveBeenCalled();
	});

	it("a second recorder cannot start while one records, and leaves its service running", async () => {
		const first = useNativeRecorder();
		expect(await first.start()).toBe(true);
		calls.length = 0;

		const second = useNativeRecorder();
		await expect(second.start()).rejects.toThrow("Es läuft bereits eine Aufnahme.");
		expect(calls).not.toContain("stopService");
		expect(calls).not.toContain("startRecording");

		await first.stop();
		expect(await second.start()).toBe(true);
		await second.stop();
	});

	describe("phone call interruptions", () => {
		function emit(name: string, event: unknown = {}): void {
			const listener = events.get(name);
			if (!listener) throw new Error(`no listener for ${name}`);
			listener(event as never);
		}

		async function wait(ms: number): Promise<void> {
			vi.setSystemTime(Date.now() + ms);
			await vi.advanceTimersByTimeAsync(1000);
		}

		it("Android: a silenced recording is paused and shown as interrupted until the user continues", async () => {
			const r = useNativeRecorder();
			await r.start();
			// Recordings already running at start belong to other apps: snapshot before the recorder starts.
			expect(silence.start.mock.invocationCallOrder[0]).toBeLessThan(recorder.startRecording.mock.invocationCallOrder[0]!);
			await wait(10_000);

			emit("silencedChanged", { silenced: true });
			expect(r.interrupted.value).toBe("call");
			expect(recorder.pauseRecording).toHaveBeenCalledTimes(1);
			expect(r.interruption.value).toBe(
				"Ein Anruf oder eine andere App hat das Mikrofon übernommen. Die Aufnahme ist angehalten; diese Zeit fehlt darin, Bisheriges bleibt erhalten.",
			);
			// The call (30 s) is not counted, as it is not in the audio; nor is the meter read.
			recorder.getCurrentAmplitude.mockClear();
			await wait(30_000);
			expect(r.elapsed.value).toBe(11);
			expect(recorder.getCurrentAmplitude).not.toHaveBeenCalled();
			// The call ending changes nothing: the user decides.
			emit("silencedChanged", { silenced: false });
			expect(r.interrupted.value).toBe("call");
			// Pause is not offered while interrupted.
			r.pause();
			expect(r.paused.value).toBe(false);

			expect(await r.continueRecording()).toBe(true);
			expect(recorder.resumeRecording).toHaveBeenCalledTimes(1);
			expect(r.interrupted.value).toBeNull();
			expect(r.interruption.value).toBeNull();
			await wait(5000);
			expect(r.elapsed.value).toBe(17);

			await r.stop();
			expect(silence.stop).toHaveBeenCalled();
			expect(events.size).toBe(0);
		});

		it("Android: continuing while the call still holds the microphone interrupts again", async () => {
			const r = useNativeRecorder();
			await r.start();
			emit("silencedChanged", { silenced: true });
			recorder.resumeRecording.mockImplementationOnce(async () => {
				// The resumed recording is silenced again; reported before resume resolves.
				emit("silencedChanged", { silenced: true });
			});

			expect(await r.continueRecording()).toBe(true);

			expect(r.interrupted.value).toBe("call");
			expect(recorder.pauseRecording).toHaveBeenCalledTimes(2);
			await r.stop();
		});

		it("Android: a recording started during a call is interrupted from the start", async () => {
			recorder.startRecording.mockImplementationOnce(async () => {
				calls.push("startRecording");
				emit("silencedChanged", { silenced: true });
			});
			const r = useNativeRecorder();

			expect(await r.start()).toBe(true);

			expect(r.interrupted.value).toBe("call");
			expect(recorder.pauseRecording).toHaveBeenCalledTimes(1);
			await r.stop();
		});

		it("Android: when the recorder cannot pause, the notice says the call is in the audio as silence", async () => {
			recorder.pauseRecording.mockRejectedValueOnce(new Error("pause failed"));
			const r = useNativeRecorder();
			await r.start();

			emit("silencedChanged", { silenced: true });
			await vi.waitFor(() => expect(r.interruptError.value).not.toBeNull());
			expect(r.interruption.value).toContain("als Stille darin");
			// It kept recording, so the time counts.
			await wait(20_000);
			expect(r.elapsed.value).toBe(21);

			expect(await r.continueRecording()).toBe(true);
			expect(recorder.resumeRecording).not.toHaveBeenCalled();
			expect(r.interrupted.value).toBeNull();
			await r.stop();
		});

		it("stopping while interrupted keeps the audio up to the call and clears the interruption", async () => {
			const r = useNativeRecorder();
			await r.start();
			await wait(10_000);
			emit("silencedChanged", { silenced: true });
			await wait(30_000);

			const blob = await r.stop();

			expect(await blob.text()).toBe("aac-bytes");
			expect(r.elapsed.value).toBe(11);
			expect(r.interrupted.value).toBeNull();
			expect(r.recording.value).toBe(false);
			expect(silence.stop).toHaveBeenCalled();
		});

		it("Android: a silencing delivered late says the call is partly in the audio as silence, and counts it", async () => {
			const r = useNativeRecorder();
			await r.start();
			await wait(10_000);
			const callStarted = Date.now();
			// The web view stalled while the dialer was up: the event arrives 20 s after the call began.
			await wait(20_000);

			emit("silencedChanged", { silenced: true, at: callStarted });

			expect(r.interrupted.value).toBe("call");
			expect(recorder.pauseRecording).toHaveBeenCalledTimes(1);
			expect(r.interruption.value).toContain("ein Teil davon ist als Stille darin");
			expect(r.interruption.value).not.toContain("fehlt darin");
			// The 20 s of silence are in the audio, so the clock has them; the time after the pause is not.
			await wait(30_000);
			expect(r.elapsed.value).toBe(32);
			await r.stop();
		});

		it("Android: a silencing delivered promptly says the call is missing", async () => {
			const r = useNativeRecorder();
			await r.start();
			const at = Date.now();
			vi.setSystemTime(at + 500);

			emit("silencedChanged", { silenced: true, at });

			expect(r.interruption.value).toContain("diese Zeit fehlt darin");
			await r.stop();
		});

		it("iOS: the plugin's interruption events interrupt; a resume after the call is paused again for the user", async () => {
			platform.name = "ios";
			const r = useNativeRecorder();
			await r.start();
			expect(silence.start).not.toHaveBeenCalled();

			emit("recordingInterruptionBegan");
			expect(r.interrupted.value).toBe("call");
			// The plugin paused itself.
			expect(recorder.pauseRecording).not.toHaveBeenCalled();

			emit("recordingInterruptionEnded", { shouldResume: true });
			expect(recorder.pauseRecording).toHaveBeenCalledTimes(1);
			expect(r.interrupted.value).toBe("call");
			// The plugin resumed before the app paused it again: no promise that the time is missing.
			expect(r.interruption.value).toBe(
				"Ein Anruf oder eine andere App hat das Mikrofon übernommen. Die Aufnahme ist angehalten, Bisheriges bleibt erhalten.",
			);

			expect(await r.continueRecording()).toBe(true);
			expect(recorder.resumeRecording).toHaveBeenCalledTimes(1);
			expect(r.interrupted.value).toBeNull();

			await r.stop();
			expect(events.size).toBe(0);
		});

		it("iOS: without shouldResume the plugin stays paused; a refused resume keeps the interruption with a reason", async () => {
			platform.name = "ios";
			const r = useNativeRecorder();
			await r.start();
			emit("recordingInterruptionBegan");
			emit("recordingInterruptionEnded", { shouldResume: false });
			expect(recorder.pauseRecording).not.toHaveBeenCalled();

			recorder.resumeRecording.mockRejectedValueOnce(new Error("Failed to start a new recording segment after interruption."));
			expect(await r.continueRecording()).toBe(false);
			expect(r.interrupted.value).toBe("call");
			expect(r.interruption.value).toContain("Die Aufnahme konnte nicht fortgesetzt werden.");

			expect(await r.continueRecording()).toBe(true);
			expect(r.interrupted.value).toBeNull();
			await r.stop();
		});

		it("iOS: an end without a begin (the recorder failed) interrupts and stops the clock", async () => {
			platform.name = "ios";
			const r = useNativeRecorder();
			await r.start();
			await wait(10_000);

			emit("recordingInterruptionEnded", { shouldResume: false });

			expect(r.interrupted.value).toBe("call");
			await wait(30_000);
			expect(r.elapsed.value).toBe(11);
			await r.stop();
		});

		it("iOS: if the plugin cannot be paused after the call, it records on without the interruption", async () => {
			platform.name = "ios";
			const r = useNativeRecorder();
			await r.start();
			await wait(10_000);
			emit("recordingInterruptionBegan");
			await wait(30_000);
			recorder.pauseRecording.mockRejectedValueOnce(new Error("No active recording to pause."));

			emit("recordingInterruptionEnded", { shouldResume: true });

			await vi.waitFor(() => expect(r.interrupted.value).toBeNull());
			await wait(5000);
			await r.stop();
			// The call is not counted; the time after it is.
			expect(r.elapsed.value).toBe(17);
		});
	});
});
