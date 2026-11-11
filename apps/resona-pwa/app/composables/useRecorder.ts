import { computed, ref, onScopeDispose } from "vue";
import type { ComputedRef, Ref } from "vue";
import {
	appendUnfinishedChunk,
	beginUnfinished,
	claimUnfinished,
	releaseUnfinished,
	type UnfinishedKind,
} from "~/data/unfinishedRecordings";
import { mergeToWav } from "~/utils/audioMerge";
import { useWakeLock } from "~/composables/useWakeLock";

/** Container formats the recorder tries, in order; it takes the first the browser supports. */
export const RECORDER_MIME_CANDIDATES = [
	"audio/webm;codecs=opus",
	"audio/webm",
	"audio/ogg;codecs=opus",
	"audio/mp4",
] as const;

/** Chunk length: a killed page loses at most this much of a persisted recording. */
export const RECORDER_TIMESLICE_MS = 4000;

/** Why recording stopped by itself: the track went silent, ended, or the page was hidden. */
export type InterruptReason = "muted" | "ended" | "hidden";

const INTERRUPT_TEXT: Record<InterruptReason, string> = {
	muted: "Das Mikrofon liefert keinen Ton mehr, z. B. wegen eines Anrufs.",
	ended: "Das Mikrofon wurde getrennt.",
	hidden: "Die App war im Hintergrund.",
};

export interface RecorderOptions {
	/** Keep the audio in IndexedDB while recording, so a killed page can offer it again. */
	persist?: () => { kind: UnfinishedKind; label?: string | null };
	/**
	 * For a caller's stream (`start(stream)`): opens a fresh one after an interruption and
	 * hands it to the caller's other consumers too. Without it a caller's stream is never
	 * interrupted, since the recorder cannot reopen it.
	 */
	reopen?: () => Promise<MediaStream>;
}

function getSupportedMime(): string {
	if (typeof MediaRecorder !== "undefined" && typeof MediaRecorder.isTypeSupported === "function") {
		for (const type of RECORDER_MIME_CANDIDATES) {
			if (MediaRecorder.isTypeSupported(type)) {
				return type;
			}
		}
	}
	return "";
}

function getAudioContextCtor(): typeof AudioContext | undefined {
	const g = globalThis as Record<string, unknown>;
	return (g.AudioContext ?? g.webkitAudioContext) as typeof AudioContext | undefined;
}

async function openMicrophone(): Promise<MediaStream> {
	// Browsers hide mediaDevices outside a secure context (HTTPS or localhost),
	// e.g. on http://<hostname>:7730 from another device.
	// lib.dom types mediaDevices as always present; at runtime it can be undefined.
	const media: MediaDevices | undefined = navigator.mediaDevices;
	if (!media?.getUserMedia) {
		throw new Error("Mikrofon nicht verfügbar – die App muss über HTTPS oder localhost geöffnet sein.");
	}
	try {
		return await media.getUserMedia({ audio: true });
	}
	catch (err) {
		throw new Error(
			err instanceof DOMException && err.name === "NotAllowedError"
				? "Mikrofonzugriff wurde verweigert."
				: "Mikrofon konnte nicht gestartet werden.",
			{ cause: err },
		);
	}
}

function releaseStream(s: MediaStream): void {
	for (const track of s.getTracks()) {
		track.stop();
	}
}

/**
 * Records the microphone (or a caller's stream) in timeslices. A recording may
 * consist of several parts: when the microphone goes silent (`mute`), ends, or
 * the page is hidden while recording, the open part is closed and the recorder
 * waits in `interrupted` (nothing is recorded meanwhile) until
 * `continueRecording()` gets a new stream for a new part or `stop()` ends it.
 * A caller's stream is only watched with `reopen`. `stop()` returns one blob:
 * the single part as recorded, several parts merged into one 16 kHz WAV. With
 * `persist`, every chunk also goes to IndexedDB (`data/unfinishedRecordings.ts`);
 * the caller discards the session (`sessionId`) once the audio is stored for good.
 */
export function useRecorder(options: RecorderOptions = {}) {
	const recording: Ref<boolean> = ref(false);
	const paused: Ref<boolean> = ref(false);
	const elapsed: Ref<number> = ref(0);
	const levels: Ref<number[]> = ref([]);
	const interrupted: Ref<InterruptReason | null> = ref(null);
	// Why continuing after an interruption failed (German), else null.
	const interruptError: Ref<string | null> = ref(null);
	/** Persisted session of the current (or last) recording, null without `persist`. */
	const sessionId: Ref<string | null> = ref(null);
	const interruption: ComputedRef<string | null> = computed(() => {
		if (!interrupted.value) return null;
		return `${INTERRUPT_TEXT[interrupted.value]} ${interruptError.value ?? "Bisher Aufgenommenes bleibt erhalten."}`;
	});

	const wake = useWakeLock();

	let active: MediaRecorder | null = null;
	// Resolves once the last closed part delivered its final chunk.
	let partEnded: Promise<void> = Promise.resolve();
	let parts: Blob[][] = [];
	let recordingMime = "";
	let stream: MediaStream | null = null;
	let ownsStream = false;
	// The recorder can get a new microphone stream after an interruption (its own, or `reopen`).
	let resumable = false;
	// The current recording runs on a stream the caller passed to `start()`.
	let callerStream = false;
	let continuing = false;
	let persistChain: Promise<void> = Promise.resolve();
	const claimed = new Set<string>();
	// Bumped by every start() and stop(): a start whose microphone opens late sees it changed.
	let startGen = 0;

	let timer: ReturnType<typeof setInterval> | null = null;

	let audioCtx: AudioContext | null = null;
	let analyser: AnalyserNode | null = null;
	let source: MediaStreamAudioSourceNode | null = null;
	let rafTimer: ReturnType<typeof setInterval> | null = null;

	function startTimer(): void {
		if (timer !== null) {
			return;
		}
		timer = setInterval(() => {
			if (!paused.value) {
				elapsed.value += 1;
			}
		}, 1000);
	}

	function stopTimer(): void {
		if (timer !== null) {
			clearInterval(timer);
			timer = null;
		}
	}

	function startLevels(src: MediaStream): void {
		const Ctor = getAudioContextCtor();
		if (!Ctor) {
			// happy-dom / unsupported browser: leave levels empty.
			return;
		}
		try {
			audioCtx = new Ctor();
			analyser = audioCtx.createAnalyser();
			analyser.fftSize = 256;
			source = audioCtx.createMediaStreamSource(src);
			source.connect(analyser);

			const buf = new Uint8Array(analyser.fftSize);
			rafTimer = setInterval(() => {
				if (!analyser || paused.value) {
					return;
				}
				analyser.getByteTimeDomainData(buf);
				let sum = 0;
				for (let i = 0; i < buf.length; i += 1) {
					const v = (buf[i]! - 128) / 128;
					sum += v * v;
				}
				const rms = Math.sqrt(sum / buf.length);
				const next = [...levels.value, rms];
				levels.value = next.slice(-100);
			}, 100);
		}
		catch {
			// Audio analysis is best-effort; never let it break recording.
			teardownLevels();
		}
	}

	function teardownLevels(): void {
		if (rafTimer !== null) {
			clearInterval(rafTimer);
			rafTimer = null;
		}
		try {
			source?.disconnect();
			analyser?.disconnect();
		}
		catch {
			// nodes may already be detached
		}
		if (audioCtx && audioCtx.state !== "closed") {
			void audioCtx.close();
		}
		source = null;
		analyser = null;
		audioCtx = null;
	}

	function onMute(): void {
		void interrupt("muted");
	}

	function onEnded(): void {
		void interrupt("ended");
	}

	function onPageHidden(e: Event): void {
		// pagehide can fire while visibilityState still reads "visible" (bfcache, iOS).
		if (e.type === "pagehide" || document.visibilityState === "hidden") void interrupt("hidden");
	}

	function watchStream(s: MediaStream): void {
		for (const track of s.getTracks()) {
			track.addEventListener?.("mute", onMute);
			track.addEventListener?.("ended", onEnded);
		}
	}

	function unwatchStream(s: MediaStream): void {
		for (const track of s.getTracks()) {
			track.removeEventListener?.("mute", onMute);
			track.removeEventListener?.("ended", onEnded);
		}
	}

	function watchPage(on: boolean): void {
		if (typeof document === "undefined" || typeof window === "undefined") return;
		if (on) {
			document.addEventListener("visibilitychange", onPageHidden);
			window.addEventListener("pagehide", onPageHidden);
		}
		else {
			document.removeEventListener("visibilitychange", onPageHidden);
			window.removeEventListener("pagehide", onPageHidden);
		}
	}

	function stopTracks(): void {
		if (stream) {
			unwatchStream(stream);
			if (ownsStream) {
				releaseStream(stream);
			}
		}
		stream = null;
		ownsStream = false;
	}

	function cleanup(): void {
		stopTimer();
		teardownLevels();
		watchPage(false);
		stopTracks();
		active = null;
		resumable = false;
	}

	function persist(task: () => Promise<void>): void {
		persistChain = persistChain.then(task).catch((err: unknown) => {
			// Best effort: the in-memory chunks still make up the recording.
			console.warn("[recorder] could not persist audio", err);
		});
	}

	/** Records `s` as a new part of the current recording. */
	function startPart(s: MediaStream): void {
		const index = parts.length;
		const chunks: Blob[] = [];
		parts.push(chunks);
		const session = sessionId.value;
		let seq = 0;
		const rec = new MediaRecorder(s, recordingMime ? { mimeType: recordingMime } : undefined);
		// Without a supported candidate the browser picks the container; keep what it chose.
		if (!recordingMime && rec.mimeType) recordingMime = rec.mimeType;
		rec.ondataavailable = (e: BlobEvent) => {
			if (!e.data || e.data.size === 0) return;
			chunks.push(e.data);
			if (session) {
				const chunk = e.data;
				const n = seq++;
				const at = elapsed.value;
				persist(() => appendUnfinishedChunk(session, index, n, chunk, at));
			}
		};
		partEnded = new Promise<void>((resolve) => {
			rec.onstop = () => resolve();
		});
		rec.start(RECORDER_TIMESLICE_MS);
		active = rec;
	}

	/** Closes the open part; its last chunk arrives before `partEnded` resolves. */
	function endPart(): Promise<void> {
		const rec = active;
		active = null;
		if (rec && rec.state !== "inactive") {
			rec.stop();
		}
		return partEnded;
	}

	async function interrupt(reason: InterruptReason): Promise<void> {
		if (!recording.value || !resumable || interrupted.value) return;
		// Paused records nothing; only a lost microphone needs the user then.
		if (reason === "hidden" && paused.value) return;
		interrupted.value = reason;
		interruptError.value = null;
		paused.value = false;
		stopTimer();
		teardownLevels();
		const ending = endPart();
		stopTracks();
		await ending;
	}

	/**
	 * Starts recording; false when it did not start: already recording, or a
	 * `stop()` or newer `start()` came while the microphone was still opening
	 * (that late stream's tracks are stopped, nothing else is touched).
	 */
	async function start(existing?: MediaStream): Promise<boolean> {
		if (recording.value) {
			return false;
		}
		startGen += 1;
		const gen = startGen;

		let next: MediaStream;
		let owned: boolean;
		if (existing) {
			next = existing;
			owned = false;
		}
		else {
			next = await openMicrophone();
			owned = true;
			if (gen !== startGen || recording.value) {
				// Superseded while the microphone opened: release this stream only.
				releaseStream(next);
				return false;
			}
		}
		// A previous stream (should not exist here) is released, never leaked.
		stopTracks();
		stream = next;
		ownsStream = owned;
		resumable = owned || options.reopen !== undefined;
		callerStream = !owned;

		parts = [];
		partEnded = Promise.resolve();
		recordingMime = getSupportedMime();
		interrupted.value = null;
		interruptError.value = null;
		sessionId.value = null;
		const meta = options.persist?.();
		const id = meta ? crypto.randomUUID() : null;
		sessionId.value = id;
		try {
			startPart(next);
		}
		catch (err) {
			// No MediaRecorder for this stream (e.g. an unsupported container): nothing to keep or hold.
			sessionId.value = null;
			stopTracks();
			throw err;
		}
		if (id) {
			claimed.add(id);
			claimUnfinished(id);
		}
		if (meta && id) {
			const now = new Date().toISOString();
			const mime = recordingMime;
			persist(() => beginUnfinished({ id, kind: meta.kind, label: meta.label ?? null, mime, startedAt: now, updatedAt: now, elapsed: 0 }));
		}

		elapsed.value = 0;
		levels.value = [];
		recording.value = true;
		paused.value = false;

		if (resumable) {
			watchStream(next);
			watchPage(true);
		}
		wake.acquire();
		startTimer();
		startLevels(next);
		return true;
	}

	/**
	 * After an interruption: opens the microphone again and records a new part of
	 * the same recording. False (with `interruptError` set) when the microphone
	 * cannot be opened, or when `stop()` came meanwhile.
	 */
	async function continueRecording(): Promise<boolean> {
		if (!recording.value || !interrupted.value || continuing) return false;
		continuing = true;
		interruptError.value = null;
		const gen = startGen;
		let next: MediaStream;
		try {
			next = callerStream ? await options.reopen!() : await openMicrophone();
		}
		catch (err) {
			if (gen === startGen) interruptError.value = err instanceof Error ? err.message : String(err);
			return false;
		}
		finally {
			continuing = false;
		}
		if (gen !== startGen || !recording.value) {
			releaseStream(next);
			return false;
		}
		stream = next;
		ownsStream = !callerStream;
		watchStream(next);
		startPart(next);
		interrupted.value = null;
		startTimer();
		startLevels(next);
		return true;
	}

	function pause(): void {
		if (!recording.value || paused.value || interrupted.value) {
			return;
		}
		active?.pause();
		paused.value = true;
	}

	function resume(): void {
		if (!recording.value || !paused.value) {
			return;
		}
		active?.resume();
		paused.value = false;
	}

	async function stop(): Promise<Blob> {
		// A start (or continue) still waiting for the microphone is now stale.
		startGen += 1;
		if (!recording.value) {
			return new Blob([], { type: recordingMime });
		}
		const mime = recordingMime;
		const ending = endPart();
		// Tracks are released only after the recorder stopped, so its last chunk is complete.
		await ending;
		const blobs = parts.filter(chunks => chunks.length > 0).map(chunks => new Blob(chunks, { type: mime }));
		parts = [];
		recording.value = false;
		paused.value = false;
		interrupted.value = null;
		interruptError.value = null;
		cleanup();
		wake.release();
		await persistChain;
		if (blobs.length <= 1) {
			return blobs[0] ?? new Blob([], { type: mime });
		}
		try {
			return (await mergeToWav(blobs)).blob;
		}
		catch (err) {
			// Keeps every byte; players may only play the first part.
			console.warn("[recorder] could not merge parts", err);
			return new Blob(blobs, { type: mime });
		}
	}

	onScopeDispose(() => {
		startGen += 1;
		const rec = active;
		cleanup();
		if (rec && rec.state !== "inactive") rec.stop();
		wake.release();
		// Sessions the caller did not discard stay in IndexedDB and become recoverable.
		for (const id of claimed) releaseUnfinished(id);
		claimed.clear();
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
