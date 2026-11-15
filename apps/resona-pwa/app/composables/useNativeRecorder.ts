import { computed, ref, onScopeDispose } from "vue";
import type { ComputedRef, Ref } from "vue";
import { Capacitor, registerPlugin } from "@capacitor/core";
import type { PluginListenerHandle } from "@capacitor/core";
import { CapacitorAudioRecorder, RecordingStatus } from "@capgo/capacitor-audio-recorder";
import { ForegroundService, Importance, ServiceType } from "@capawesome-team/capacitor-android-foreground-service";
import { Filesystem } from "@capacitor/filesystem";
import type { Recorder } from "./useRecorder";
import { nativeSessionId } from "~/data/nativeUnfinished";
import { describeInterruption, type InterruptReason } from "~/utils/recorderInterruption";

/**
 * App-local Android plugin (`android/app/src/main/java/de/ecsplico/resona/RecordingSilencePlugin.java`):
 * reports whether Android silences this app's microphone recording, which it does for the
 * length of a phone call (Android 10+, `AudioRecordingConfiguration.isClientSilenced()`).
 * `start()` before the recorder starts (recordings already running then are not ours),
 * `stop()` once it stopped. `supported` is false below Android 10. `at` = native wall-clock
 * time (ms) of the change: the web view may deliver the event much later (it stalls while
 * the dialer or lock screen is up).
 */
interface RecordingSilencePlugin {
	start(): Promise<{ supported: boolean }>;
	stop(): Promise<void>;
	addListener(eventName: "silencedChanged", listener: (event: { silenced: boolean; at?: number }) => void): Promise<PluginListenerHandle>;
}

const RecordingSilence = registerPlugin<RecordingSilencePlugin>("RecordingSilence");

/**
 * A silencing that reaches the app later than this was recorded as silence until the app
 * paused the recording (and the clock counted it), so the notice must not say it is missing.
 */
export const LATE_SIGNAL_MS = 3000;

const LATE_TEXT = "Der Anruf wurde erst verspätet erkannt: ein Teil davon ist als Stille darin, Bisheriges bleibt erhalten.";

/** iOS: after a recorder failure the plugin cannot resume; stopping keeps what it recorded. */
const FAILED_TEXT = "Die Aufnahme wurde abgebrochen und lässt sich nicht fortsetzen. Mit Beenden bleibt alles bisher Aufgenommene erhalten.";

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
 * Interruptions: a phone call. Locking the phone does not interrupt (the foreground
 * service keeps the microphone). On Android 10+ the OS feeds the recording silence during
 * a call; the app-local `RecordingSilence` plugin reports that, and the recorder pauses
 * the native recording. On iOS the plugin pauses by itself (`recordingInterruptionBegan`)
 * and may resume when the call ends (`recordingInterruptionEnded.shouldResume`); the
 * recorder pauses it again. Either way `interrupted` is `"call"` and the clock stands
 * until the user continues (`continueRecording()` resumes the same file) or stops, so the
 * call is not in the audio and the notice says so. Should Android refuse to pause, the
 * notice says the call is in the audio as silence instead.
 *
 * No IndexedDB chunk persistence: the audio is a native ADTS file, which a killed app
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
	// Android pauses the native recording for the interruption, so the notice says the call is
	// not in it. iOS: the plugin resumes on its own after a call before the app can pause it
	// again, so the notice promises nothing about the audio.
	const interruption: ComputedRef<string | null> = computed(() =>
		interrupted.value
			? describeInterruption(
					interrupted.value,
					interruptError.value ?? (isAndroid() ? "Die Aufnahme ist angehalten; diese Zeit fehlt darin, Bisheriges bleibt erhalten." : "Die Aufnahme ist angehalten, Bisheriges bleibt erhalten."),
				)
			: null);

	// A native recording is running (set once startRecording resolved).
	let active = false;
	// Bumped by every start() and stop(): a start whose native call returns late sees it changed.
	let startGen = 0;
	let startedAt = 0;
	// Total time the clock stood (pauses, interruptions), and since when it stands now.
	let pausedMs = 0;
	let haltedAt: number | null = null;
	let clock: ReturnType<typeof setInterval> | null = null;
	let meter: ReturnType<typeof setInterval> | null = null;
	let metering = false;
	// The native recording is paused because of the interruption (continuing resumes it).
	let nativePaused = false;
	// continueRecording() is resuming the native recording.
	let resuming = false;
	// Latest interruption state reported while starting or resuming, applied afterwards, and the
	// native time it began (Android; null when unknown).
	let pendingCall = false;
	let pendingAt: number | null = null;
	// iOS: the recorder failed (`recordingError`); the plugin cannot resume, only stop.
	let failed = false;
	let listeners: PluginListenerHandle[] = [];

	function isAndroid(): boolean {
		return Capacitor.getPlatform() === "android";
	}

	function tick(): void {
		const now = Date.now();
		const activeMs = now - startedAt - pausedMs - (haltedAt === null ? 0 : now - haltedAt);
		elapsed.value = Math.max(0, Math.floor(activeMs / 1000));
	}

	function halt(): void {
		if (haltedAt === null) haltedAt = Date.now();
	}

	function unhalt(): void {
		if (haltedAt === null) return;
		pausedMs += Date.now() - haltedAt;
		haltedAt = null;
	}

	function startTimers(): void {
		clock = setInterval(tick, 1000);
		meter = setInterval(() => {
			if (metering || paused.value || interrupted.value || (typeof document !== "undefined" && document.visibilityState !== "visible")) {
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

	/** The microphone was taken (true) or given back (false) by a call, natively at `at`. */
	function signal(taken: boolean, at: number | null = null): void {
		if (!active || resuming) {
			pendingCall = taken;
			pendingAt = taken ? at : null;
			return;
		}
		if (taken) interrupt(at);
	}

	/**
	 * Stops the clock and shows the interruption until the user continues or stops. Android
	 * keeps recording silence, so the native recording is paused here; iOS paused it itself.
	 * `at`: when the call took the microphone; reported late, the silence until now is in
	 * the audio and counted, and the notice says so.
	 */
	function interrupt(at: number | null = null): void {
		if (!active || paused.value || interrupted.value) return;
		halt();
		interrupted.value = "call";
		interruptError.value = at !== null && Date.now() - at > LATE_SIGNAL_MS ? LATE_TEXT : null;
		nativePaused = true;
		if (!isAndroid()) return;
		void CapacitorAudioRecorder.pauseRecording().catch(() => {
			if (!active || interrupted.value !== "call" || !nativePaused) return;
			// Still recording: the call goes into the audio as silence, and its time counts.
			nativePaused = false;
			haltedAt = null;
			interruptError.value = "Die Aufnahme ließ sich nicht anhalten: diese Zeit ist als Stille darin.";
		});
	}

	/**
	 * iOS: the call ended; `resumed` = the plugin records again on its own. The plugin also
	 * sends an end without `shouldResume` when it was not paused by the interruption, e.g.
	 * late, after the user already continued: that counts only when the plugin is paused.
	 */
	function interruptionEnded(resumed: boolean): void {
		if (!active || resuming) {
			if (resumed) {
				pendingCall = false;
				pendingAt = null;
			}
			else {
				void ifNativePaused();
			}
			return;
		}
		if (!resumed) {
			if (!interrupted.value) void ifNativePaused();
			return;
		}
		if (interrupted.value !== "call") return;
		// It records again from now: the clock counts that until the pause below lands.
		unhalt();
		// Wait for the user, as after any interruption.
		void CapacitorAudioRecorder.pauseRecording()
			.then(() => {
				if (active && interrupted.value === "call") halt();
			})
			.catch(() => {
				if (!active || interrupted.value !== "call") return;
				// It records again: the call is not in the audio, what follows is.
				nativePaused = false;
				interrupted.value = null;
				interruptError.value = null;
			});
	}

	/** An end without a begin interrupts only when the plugin really is paused. */
	async function ifNativePaused(): Promise<void> {
		const { status } = await CapacitorAudioRecorder.getRecordingStatus().catch(() => ({ status: null }));
		if (status === RecordingStatus.Paused) signal(true);
	}

	/** iOS: the recorder broke (an interruption it could not survive, an encoding error). */
	function recorderFailed(): void {
		if (!active) return;
		failed = true;
		if (paused.value) paused.value = false;
		interrupt();
		interruptError.value = FAILED_TEXT;
	}

	/** Inside the lane, before the recorder starts: listens for calls taking the microphone. */
	async function watchInterruptions(): Promise<void> {
		pendingCall = false;
		pendingAt = null;
		const handles = isAndroid()
			? [RecordingSilence.addListener("silencedChanged", ({ silenced, at }) => signal(silenced, at ?? null))]
			: [
					CapacitorAudioRecorder.addListener("recordingInterruptionBegan", () => signal(true)),
					CapacitorAudioRecorder.addListener("recordingInterruptionEnded", ({ shouldResume }) => interruptionEnded(shouldResume)),
					CapacitorAudioRecorder.addListener("recordingError", () => recorderFailed()),
				];
		const settled = await Promise.allSettled(handles);
		listeners = settled.flatMap(s => (s.status === "fulfilled" ? [s.value] : []));
		if (isAndroid()) {
			// Without it (an older app build) the recording runs, as before, undetected.
			await RecordingSilence.start().catch(() => undefined);
		}
	}

	async function unwatchInterruptions(): Promise<void> {
		const handles = listeners;
		listeners = [];
		await Promise.all(handles.map(h => h.remove().catch(() => undefined)));
		if (isAndroid()) {
			await RecordingSilence.stop().catch(() => undefined);
		}
	}

	function clearInterruption(): void {
		interrupted.value = null;
		interruptError.value = null;
		nativePaused = false;
		pendingCall = false;
		pendingAt = null;
		haltedAt = null;
		failed = false;
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
			await watchInterruptions();
			try {
				await CapacitorAudioRecorder.startRecording({ format: NATIVE_RECORDING_FORMAT });
			}
			catch (err) {
				await unwatchInterruptions();
				await stopRecordingService();
				throw new Error("Mikrofon konnte nicht gestartet werden.", { cause: err });
			}
			if (gen !== startGen || recording.value) {
				await CapacitorAudioRecorder.cancelRecording().catch(() => undefined);
				await unwatchInterruptions();
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
		// Started while a call already had the microphone: Android records silence from the start.
		const callAtStart = pendingCall;
		const callAt = pendingAt;
		clearInterruption();
		elapsed.value = 0;
		levels.value = [];
		recording.value = true;
		paused.value = false;
		startTimers();
		if (callAtStart) interrupt(callAt);
		return true;
	}

	function pause(): void {
		if (!recording.value || paused.value || interrupted.value) {
			return;
		}
		halt();
		paused.value = true;
		void CapacitorAudioRecorder.pauseRecording().catch(() => {
			// Still recording natively: show it, so the clock matches the audio.
			if (recording.value && paused.value) {
				unhalt();
				paused.value = false;
			}
		});
	}

	function resume(): void {
		if (!recording.value || !paused.value || interrupted.value) {
			return;
		}
		unhalt();
		paused.value = false;
		void CapacitorAudioRecorder.resumeRecording().catch(() => {
			// Still paused natively: show it rather than count time that is not recorded.
			if (recording.value && !paused.value && !interrupted.value) {
				halt();
				paused.value = true;
			}
		});
	}

	/**
	 * After an interruption: resumes the native recording into the same file (the call is
	 * not in it). False (with `interruptError` set) when the recorder refuses, e.g. while a
	 * call still holds the microphone on iOS; Android reports a call still going and
	 * interrupts again.
	 */
	async function continueRecording(): Promise<boolean> {
		if (!active || !interrupted.value || resuming) {
			return false;
		}
		if (failed) {
			interruptError.value = FAILED_TEXT;
			return false;
		}
		interruptError.value = null;
		if (nativePaused) {
			resuming = true;
			pendingCall = false;
			pendingAt = null;
			try {
				await CapacitorAudioRecorder.resumeRecording();
			}
			catch {
				if (active) interruptError.value = failed ? FAILED_TEXT : "Die Aufnahme konnte nicht fortgesetzt werden.";
				return false;
			}
			finally {
				resuming = false;
			}
			if (!active) {
				return false;
			}
			nativePaused = false;
		}
		unhalt();
		interrupted.value = null;
		const callStill = pendingCall;
		const callAt = pendingAt;
		pendingCall = false;
		pendingAt = null;
		if (callStill) interrupt(callAt);
		return true;
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
				await unwatchInterruptions();
				await stopRecordingService().catch(() => undefined);
			}
		});
		recording.value = false;
		paused.value = false;
		clearInterruption();
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
				await unwatchInterruptions();
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
		interruption,
		sessionId,
		start,
		pause,
		resume,
		continueRecording,
		stop,
	};
}
