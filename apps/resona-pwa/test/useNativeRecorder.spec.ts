import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useNativeRecorder } from "~/composables/useNativeRecorder";

// One ordered log of native calls: the foreground service must be up before the
// recorder starts (Android cuts the mic of a locked phone otherwise).
const { calls, recorder, service, filesystem } = vi.hoisted(() => {
	const calls: string[] = [];
	const log = <T>(name: string, value?: T) => vi.fn(async () => {
		calls.push(name);
		return value as T;
	});
	return {
		calls,
		recorder: {
			requestPermissions: log("requestPermissions", { recordAudio: "granted" }),
			startRecording: log("startRecording"),
			pauseRecording: log("pauseRecording"),
			resumeRecording: log("resumeRecording"),
			stopRecording: log("stopRecording", { uri: "file:///cache/rec.m4a", duration: 0 }),
			cancelRecording: log("cancelRecording"),
			getCurrentAmplitude: vi.fn(async () => ({ value: 0.5 })),
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
		getPlatform: () => "android",
		convertFileSrc: (uri: string) => uri.replace("file://", "https://localhost/_capacitor_file_"),
	},
}));
vi.mock("@capgo/capacitor-audio-recorder", () => ({ CapacitorAudioRecorder: recorder }));
vi.mock("@capawesome-team/capacitor-android-foreground-service", () => ({
	ForegroundService: service,
	Importance: { Low: 2 },
	ServiceType: { Microphone: 128 },
}));
vi.mock("@capacitor/filesystem", () => ({ Filesystem: filesystem }));

const fetchMock = vi.fn(async () => new Response("m4a-bytes"));

describe("useNativeRecorder", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		calls.length = 0;
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

	it("records inside the microphone service and returns the file as audio/mp4", async () => {
		const r = useNativeRecorder();
		expect(await r.start()).toBe(true);
		expect(r.recording.value).toBe(true);

		const blob = await r.stop();

		expect(calls.indexOf("startService")).toBeLessThan(calls.indexOf("startRecording"));
		expect(calls.indexOf("stopRecording")).toBeLessThan(calls.indexOf("stopService"));
		expect(service.startForegroundService).toHaveBeenCalledWith(expect.objectContaining({ serviceType: 128 }));
		expect(fetchMock).toHaveBeenCalledWith("https://localhost/_capacitor_file_/cache/rec.m4a");
		expect(blob.type).toBe("audio/mp4");
		expect(await blob.text()).toBe("m4a-bytes");
		expect(filesystem.deleteFile).toHaveBeenCalledWith({ path: "file:///cache/rec.m4a" });
		expect(r.recording.value).toBe(false);
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
});
