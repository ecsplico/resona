import { computed, ref, onScopeDispose } from "vue";
import type { Ref } from "vue";
import { Capacitor } from "@capacitor/core";
import { CapacitorAudioRecorder } from "@capgo/capacitor-audio-recorder";
import { ForegroundService, Importance, ServiceType } from "@capawesome-team/capacitor-android-foreground-service";
import { Filesystem } from "@capacitor/filesystem";
import type { InterruptReason, Recorder } from "./useRecorder";
import { nativeSessionId } from "~/data/nativeUnfinished";

/**
 * The plugin records AAC as ADTS (`format: "aac"`, `.aac`): self-contained frames, so a
 * file the app left behind when it was killed stays readable up to its last frame and is
 * offered again (`data/nativeUnfinished.ts`). MPEG-4 writes its index only at stop.
 */
export const NATIVE_RECORDING_FORMAT = "aac";
export const NATIVE_RECORDING_MIME = "audio/aac";

const NOTIFICATION_ID = 3401;
const NOTIFICATION_CHANNEL = "recording";

/** True inside the Capacitor app shell, where the native recorder plugin is compiled in. */
export function hasNativeRecorder(): boolean {
	return Capacitor.isNativePlatform() && Capacitor.isPluginAvailable("CapacitorAudioRecorder");
}

/**
 * Android only: a foreground service of type `microphone` with a persistent
 * notification. It keeps the process (and the plugin's MediaRecorder) alive
 * and allowed to use the microphone while the screen is locked.
 */
async function startRecordingService(): Promise<void> {
	if (Capacitor.getPlatform() !== "android") {
		return;
	}
	// Android 13+ hides the notification without this permission; the service runs anyway.
	await ForegroundService.requestPermissions().catch(() => undefined);
	await ForegroundService.createNotificationChannel({
		id: NOTIFICATION_CHANNEL,
		name: "Aufnahme",
		description: "Läuft, solange Resona aufnimmt.",
		importance: Importance.Low,
	}).catch(() => undefined);
	await ForegroundService.startForegroundService({
		id: NOTIFICATION_ID,
		title: "Resona nimmt auf",
		body: "Die Aufnahme läuft auch bei gesperrtem Bildschirm.",
		smallIcon: "ic_stat_resona",
		serviceType: ServiceType.Microphone,
		notificationChannelId: NOTIFICATION_CHANNEL,
		silent: true,
	});
}

async function stopRecordingService(): Promise<void> {
	if (Capacitor.getPlatform() === "android") {
		await ForegroundService.stopForegroundService().catch(() => undefined);
	}
}

// One native recorder and one service per app: their calls run one at a time, so
// a stale start cancelling its recording can never cancel a newer start's.
let nativeLane: Promise<unknown> = Promise.resolve();
// Some recorder instance holds the native recording (and the service); only read and
// written inside the lane, so a second instance's failed start never stops that service.
let nativeBusy = false;

function serialized<T>(task: () => Promise<T>): Promise<T> {
	const run = nativeLane.then(task, task);
	nativeLane = run.catch(() => undefined);
	return run;
}

/** The file URI an unclean Android stop still kept on disk (rejection `data.uri`). */
function uriFromError(err: unknown): string {
	const data = (err as { data?: { uri?: unknown } } | null)?.data;
	return typeof data?.uri === "string" ? data.uri : "";
}

/**
 * Same surface as `useWebRecorder`, backed by the native recorder plugin.
 * Recording runs outside the web view, so it continues while the phone is
 * locked or the app is in the background; elapsed time is wall-clock based
 * for the same reason (web view timers stall while locked).
 *
 * Not supported here: interruptions (`interrupted` stays null, `continueRecording()`
 * is false). Locking the phone does not interrupt (the foreground service keeps the
 * microphone), but a phone call does: Android then feeds silence without telling the
 * plugin, and the iOS interruption events of the plugin are not handled. Neither is
 * IndexedDB chunk persistence: the audio is a native ADTS file, which a killed app
 * leaves in the recorder's cache directory, where the recordings list offers it
 * (Android, `data/nativeUnfinished.ts`). `stop()` keeps that file and sets
 * `sessionId` to it, so the caller deletes it (`discardUnfinished`) once the audio
 * is saved or thrown away, as with a web session. Nor a
 * caller's stream (`start()` always records the microphone). A screen wake lock is not
 * needed: the service holds a partial wake lock while recording.
 */
export function useNativeRecorder(): Recorder {
	const recording: Ref<boolean> = ref(false);
	const paused: Ref<boolean> = ref(false);
	const elapsed: Ref<number> = ref(0);
	const levels: Ref<number[]> = ref([]);
	const interrupted: Ref<InterruptReason | null> = ref(null);
	const interruptError: Ref<string | null> = ref(null);
	const sessionId: Ref<string | null> = ref(null);

	// A native recording is running (set once startRecording resolved).
	let active = false;
	// Bumped by every start() and stop(): a start whose native call returns late sees it changed.
	let startGen = 0;
	let startedAt = 0;
	let pausedAt = 0;
	let pausedMs = 0;
	let clock: ReturnType<typeof setInterval> | null = null;
	let meter: ReturnType<typeof setInterval> | null = null;
	let metering = false;

	function tick(): void {
		const now = Date.now();
		const activeMs = now - startedAt - pausedMs - (paused.value ? now - pausedAt : 0);
		elapsed.value = Math.max(0, Math.floor(activeMs / 1000));
	}

	function startTimers(): void {
		clock = setInterval(tick, 1000);
		meter = setInterval(() => {
			if (metering || paused.value || (typeof document !== "undefined" && document.visibilityState !== "visible")) {
				return;
			}
			metering = true;
			CapacitorAudioRecorder.getCurrentAmplitude()
				.then(({ value }) => {
					if (active) {
						levels.value = [...levels.value, value].slice(-100);
					}
				})
				.catch(() => undefined)
				.finally(() => {
					metering = false;
				});
		}, 100);
	}

	function stopTimers(): void {
		if (clock !== null) {
			clearInterval(clock);
			clock = null;
		}
		if (meter !== null) {
			clearInterval(meter);
			meter = null;
		}
	}

	/**
	 * Starts recording; false when it did not start: already recording, or a
	 * `stop()` or newer `start()` came while the native recorder was starting
	 * (that recording is cancelled again).
	 */
	async function start(): Promise<boolean> {
		if (recording.value) {
			return false;
		}
		startGen += 1;
		const gen = startGen;
		sessionId.value = null;

		let permission: string;
		try {
			permission = (await CapacitorAudioRecorder.requestPermissions()).recordAudio;
		}
		catch (err) {
			throw new Error("Mikrofon konnte nicht gestartet werden.", { cause: err });
		}
		if (permission !== "granted") {
			throw new Error("Mikrofonzugriff wurde verweigert.");
		}
		if (gen !== startGen) {
			return false;
		}

		// Without the service Android cuts the microphone once the screen locks:
		// refuse rather than record something that silently stops.
		const started = await serialized(async () => {
			if (gen !== startGen) {
				return false;
			}
			if (nativeBusy) {
				throw new Error("Es läuft bereits eine Aufnahme.");
			}
			try {
				await startRecordingService();
			}
			catch (err) {
				throw new Error("Hintergrundaufnahme konnte nicht gestartet werden.", { cause: err });
			}
			if (gen !== startGen) {
				await stopRecordingService();
				return false;
			}
			try {
				await CapacitorAudioRecorder.startRecording({ format: NATIVE_RECORDING_FORMAT });
			}
			catch (err) {
				await stopRecordingService();
				throw new Error("Mikrofon konnte nicht gestartet werden.", { cause: err });
			}
			if (gen !== startGen || recording.value) {
				await CapacitorAudioRecorder.cancelRecording().catch(() => undefined);
				await stopRecordingService();
				return false;
			}
			nativeBusy = true;
			return true;
		});
		if (!started) {
			return false;
		}

		active = true;
		startedAt = Date.now();
		pausedMs = 0;
		elapsed.value = 0;
		levels.value = [];
		recording.value = true;
		paused.value = false;
		startTimers();
		return true;
	}

	function pause(): void {
		if (!recording.value || paused.value) {
			return;
		}
		pausedAt = Date.now();
		paused.value = true;
		void CapacitorAudioRecorder.pauseRecording().catch(() => {
			// Still recording natively: show it, so the clock matches the audio.
			if (recording.value && paused.value) {
				pausedMs += Date.now() - pausedAt;
				paused.value = false;
			}
		});
	}

	function resume(): void {
		if (!recording.value || !paused.value) {
			return;
		}
		pausedMs += Date.now() - pausedAt;
		paused.value = false;
		void CapacitorAudioRecorder.resumeRecording().catch(() => {
			// Still paused natively: show it rather than count time that is not recorded.
			if (recording.value && !paused.value) {
				pausedAt = Date.now();
				paused.value = true;
			}
		});
	}

	async function stop(): Promise<Blob> {
		// A start still waiting for the native recorder is now stale.
		startGen += 1;
		const empty = new Blob([], { type: NATIVE_RECORDING_MIME });
		if (!active) {
			return empty;
		}
		active = false;
		tick();
		stopTimers();

		// Never rejects: an unclean stop still reports the file it kept.
		const uri = await serialized(async () => {
			try {
				return (await CapacitorAudioRecorder.stopRecording()).uri ?? "";
			}
			catch (err) {
				return uriFromError(err);
			}
			finally {
				nativeBusy = false;
				await stopRecordingService().catch(() => undefined);
			}
		});
		recording.value = false;
		paused.value = false;
		if (!uri) {
			return empty;
		}

		let audio: Blob;
		try {
			const res = await fetch(Capacitor.convertFileSrc(uri));
			if (!res.ok) {
				return empty;
			}
			audio = new Blob([await res.arrayBuffer()], { type: NATIVE_RECORDING_MIME });
		}
		catch {
			// Unreadable: the file stays in the app cache rather than being deleted unread.
			return empty;
		}
		// The caller deletes the file once the audio is saved or discarded; until then
		// a killed app finds it again. A file the list would not offer goes now.
		const session = nativeSessionId(uri);
		if (session) sessionId.value = session;
		else void Filesystem.deleteFile({ path: uri }).catch(() => undefined);
		return audio;
	}

	onScopeDispose(() => {
		startGen += 1;
		stopTimers();
		if (active) {
			active = false;
			void serialized(async () => {
				await CapacitorAudioRecorder.cancelRecording().catch(() => undefined);
				nativeBusy = false;
				await stopRecordingService();
			});
		}
	});

	return {
		recording,
		paused,
		elapsed,
		levels,
		interrupted,
		interruptError,
		interruption: computed(() => null),
		sessionId,
		start,
		pause,
		resume,
		continueRecording: async () => false,
		stop,
	};
}
