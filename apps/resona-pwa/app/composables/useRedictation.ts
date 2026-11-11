import { computed, onScopeDispose, ref, watch, type ComputedRef, type Ref } from "vue";
import { HubError, type HubJob } from "~/composables/useHub";
import { deleteJobQuietly, isTransient, jobFailureMessage, jobReadErrorMessage } from "~/data/localProcessor";
import { discardUnfinished } from "~/data/unfinishedRecordings";
import { mergeToWav } from "~/utils/audioMerge";
import type { Recording } from "~/types/directus";

/**
 * A stopped part. `status`: its hub job runs in the background, failed and waits
 * for retry/discard, or its text landed (`done`). `audio`: its audio is being
 * appended to the recording, is saved there, or could not be saved (waits for
 * `retryAudio`). A part leaves the list once its text landed and its audio is saved.
 */
export interface RedictationPart {
	id: string;
	status: "transcribing" | "error" | "done";
	/** German message while `status` is `error`. */
	error: string | null;
	audio: "appending" | "saved" | "failed";
	/** German message while `audio` is `failed`. */
	audioError: string | null;
}

/** What happened to a part's audio after it was appended to the recording. */
export type RedictationAudio
	= | { partId: string; ok: true; audio_file: string; duration_seconds: number }
		| { partId: string; ok: false; error: string };

export interface RedictationHooks {
	/** The recording as it is now (audio file and duration change with every merge). */
	recording(): Recording | null;
	/** The part's text arrived (trimmed; empty when nothing was recognised). */
	onText(partId: string, text: string): void;
	/** The part's transcription failed; it stays for `retry` or `discard`. */
	onError(partId: string, message: string): void;
	/** A part's audio was appended to the recording (in stop order), or that failed. */
	onAudio(result: RedictationAudio): void;
}

/** The open part's meter, pause and interruption controls, for the record bar. */
export interface RedictationMeter {
	elapsed: Ref<number>;
	levels: Ref<number[]>;
	paused: Ref<boolean>;
	/** German text while the part is interrupted (microphone lost, app in background), else null. */
	interruption: ComputedRef<string | null>;
	pause(): void;
	resume(): void;
	/** Records on after an interruption; the new audio joins the same part. */
	continueRecording(): Promise<boolean>;
}

export interface Redictation {
	/** Own recorder (separate from push-to-talk): meter and pause of the open part. */
	recorder: RedictationMeter;
	/** The microphone is open (or opening) for a part. */
	recording: Ref<boolean>;
	/** Why the last part could not start or was empty (German), else null. */
	startError: Ref<string | null>;
	/** Stopped parts still transcribing or failed, in stop order. */
	parts: Ref<RedictationPart[]>;
	/** Parts whose hub job is running. */
	transcribing: ComputedRef<number>;
	/** Audio of stopped parts still being appended to the recording. */
	merging: ComputedRef<boolean>;
	/** Something would be lost by leaving: mic open, a job running or audio being appended. */
	busy: ComputedRef<boolean>;
	/** Parts that failed (text or audio) and live only in memory: leaving loses them. */
	failed: ComputedRef<number>;
	/** Opens the microphone for part `partId`; false when it could not start. */
	start(partId: string): Promise<boolean>;
	/** Closes the microphone and drops what it captured. */
	cancel(): Promise<void>;
	/** Stops the part: its audio is appended and its job starts in the background. False when it was empty. */
	finish(): Promise<boolean>;
	/** Transcribes a failed part again. */
	retry(partId: string): void;
	/** Appends a part's audio again after the append failed. */
	retryAudio(partId: string): void;
	/** Gives a failed part up (text or audio failed), dropping its audio blob. */
	discard(partId: string): void;
}

const POLL_MS = 2000;
const NO_TOKEN = "Kein Hub-Token – in Einstellungen → Verbindung eintragen, dann erneut versuchen.";
const UNREACHABLE = "Hub nicht erreichbar – Verbindung prüfen und erneut versuchen.";
const CLOSED = "Die Aufnahme ist nicht mehr geöffnet.";

function errorText(err: unknown): string {
	return err instanceof Error ? err.message : String(err);
}

/** German message for a failed transcription attempt. */
function transcribeErrorMessage(err: unknown): string {
	// useHub turns a failed fetch into status 0; a body read cut off mid-response
	// still rejects with the browser's raw TypeError.
	if ((err instanceof HubError && err.status === 0) || err instanceof TypeError) return UNREACHABLE;
	return err instanceof HubError ? `Transkription fehlgeschlagen: ${err.message}` : errorText(err);
}

/** Upload name for a recorded part, extension from its MIME type. */
function partFilename(part: Blob, recording: Recording): string {
	const ext = part.type.includes("wav") ? "wav" : part.type.includes("ogg") ? "ogg" : part.type.includes("mp4") ? "m4a" : "webm";
	return `${recording.title || recording.id}-nachdiktat.${ext}`;
}

function onBeforeUnload(event: Event): void {
	event.preventDefault();
}

/**
 * Re-dictation: records parts with its own recorder, one at a time. A stopped
 * part never blocks: its audio is appended to the recording's file (one 16 kHz
 * mono WAV, parts strictly in stop order) and its text comes from its own hub job
 * (upload → job → poll → result) in the background, while the next part can be
 * recorded right away and the user keeps typing and saving. A part keeps its audio
 * blob until its text landed and its audio is saved: a failed job waits for
 * `retry`, a failed append for `retryAudio` (appended at the end then).
 * Jobs and failed parts need the page open: `beforeunload` is guarded while
 * `busy` or a part `failed`.
 */
export function useRedictation(hooks: RedictationHooks): Redictation {
	// Every part also goes to IndexedDB while recorded, so a killed page offers it again.
	const recorder = useRecorder({
		persist: () => ({ kind: "redictation", label: `Nachdiktat ${hooks.recording()?.title ?? ""}`.trim() }),
	});
	const hub = useHub();
	const store = useDataStore();

	const recording = ref(false);
	const startError: Ref<string | null> = ref(null);
	const parts: Ref<RedictationPart[]> = ref([]);
	const pendingMerges = ref(0);
	const transcribing = computed(() => parts.value.filter(p => p.status === "transcribing").length);
	const merging = computed(() => pendingMerges.value > 0);
	const busy = computed(() => recording.value || transcribing.value > 0 || merging.value);
	const failed = computed(() => parts.value.filter(p => p.status === "error" || p.audio === "failed").length);

	// The part the microphone is open for; bumped `micAttempt` makes a pending start stale.
	let micPart: string | null = null;
	let micAttempt = 0;
	// Audio blobs of stopped parts, kept until their text landed or they are discarded.
	const blobs = new Map<string, Blob>();
	// Current attempt per part: a retry, discard or dispose makes older polls stale.
	const attempts = new Map<string, number>();
	const wakers = new Set<() => void>();
	// Persisted recorder session per stopped part, dropped once its audio is in the recording.
	const sessions = new Map<string, string>();
	let disposed = false;
	// Appends run one after another, so parts join the audio in stop order.
	let mergeChain: Promise<void> = Promise.resolve();

	if (typeof window !== "undefined") {
		watch(() => busy.value || failed.value > 0, (on) => {
			if (on) window.addEventListener("beforeunload", onBeforeUnload);
			else window.removeEventListener("beforeunload", onBeforeUnload);
		});
	}

	function setPart(id: string, patch: Partial<RedictationPart>): void {
		parts.value = parts.value.map(p => (p.id === id ? { ...p, ...patch } : p));
	}

	/** Drops a part whose text landed and whose audio is saved: nothing of it is left to lose. */
	function settle(id: string): void {
		const part = parts.value.find(p => p.id === id);
		if (part?.status === "done" && part.audio === "saved") forget(id);
	}

	function forget(id: string): void {
		parts.value = parts.value.filter(p => p.id !== id);
		blobs.delete(id);
		attempts.set(id, (attempts.get(id) ?? 0) + 1);
		for (const wake of [...wakers]) wake();
	}

	// Executor form instead of Promise.withResolvers: iOS Safari < 17.4.
	function sleep(ms: number): Promise<void> {
		return new Promise((resolve) => {
			const wake = () => {
				clearTimeout(timer);
				wakers.delete(wake);
				resolve();
			};
			const timer = setTimeout(wake, ms);
			wakers.add(wake);
		});
	}

	/** Error for a failed job or result read that is not worth retrying. */
	function readError(err: unknown, step: "job" | "result"): unknown {
		const message = jobReadErrorMessage(err, step);
		return message === null ? err : new Error(message, { cause: err });
	}

	/** Transcript of `blob` via a hub job; null when the attempt went stale. */
	async function transcribe(blob: Blob, rec: Recording, stale: () => boolean): Promise<string | null> {
		// Fresh key per attempt: a retry after a failed job must not get the failed job back.
		const key = `redictation:recordings:${rec.id}:${crypto.randomUUID()}`;
		const uploadId = await hub.upload(blob, partFilename(blob, rec));
		if (stale()) return null;
		const jobId = await hub.createJob(
			{ kind: "audio", upload_id: uploadId, language: rec.language, profile: rec.profile },
			key,
		);
		// Once the job exists it is deleted however the attempt ends: done, stale or failed.
		try {
			for (;;) {
				if (!stale()) await sleep(POLL_MS);
				if (stale()) return null;
				let job: HubJob;
				try {
					job = await hub.getJob(jobId);
				}
				catch (err) {
					if (isTransient(err)) continue;
					throw readError(err, "job");
				}
				const failure = jobFailureMessage(job);
				if (failure !== null) throw new Error(failure);
				if (job.status !== "completed" || stale()) continue;
				let markdown: string;
				try {
					markdown = (await hub.getResult(jobId)).markdown;
				}
				catch (err) {
					if (isTransient(err)) continue;
					throw readError(err, "result");
				}
				return stale() ? null : markdown.trim();
			}
		}
		finally {
			await deleteJobQuietly(hub, jobId);
		}
	}

	async function runPart(id: string): Promise<void> {
		const blob = blobs.get(id);
		const rec = hooks.recording();
		const current = (attempts.get(id) ?? 0) + 1;
		attempts.set(id, current);
		const stale = () => disposed || attempts.get(id) !== current;
		const fail = (message: string) => {
			setPart(id, { status: "error", error: message });
			hooks.onError(id, message);
		};
		if (!blob || !rec) {
			fail(CLOSED);
			return;
		}
		setPart(id, { status: "transcribing", error: null });
		if (!hub.hasToken()) {
			fail(NO_TOKEN);
			return;
		}
		let text: string | null;
		try {
			text = await transcribe(blob, rec, stale);
		}
		catch (err) {
			if (!stale()) fail(transcribeErrorMessage(err));
			return;
		}
		if (text === null || stale()) return;
		setPart(id, { status: "done", error: null });
		settle(id);
		hooks.onText(id, text);
	}

	async function loadStoredAudio(rec: Recording): Promise<Blob | null> {
		const url = await store.audioUrl(rec);
		if (!url) return null;
		try {
			const resp = await fetch(url);
			if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
			return await resp.blob();
		}
		finally {
			// The local store hands out object URLs; Directus asset URLs need no cleanup.
			if (url.startsWith("blob:")) URL.revokeObjectURL(url);
		}
	}

	/** Appends part `partId`'s `blob` to the recording's stored audio and stores the result. */
	async function appendAudio(partId: string, blob: Blob): Promise<RedictationAudio> {
		const rec = hooks.recording();
		if (!rec) return { partId, ok: false, error: CLOSED };
		let stored: Blob | null;
		try {
			stored = await loadStoredAudio(rec);
		}
		catch {
			return { partId, ok: false, error: "Bisheriges Audio konnte nicht geladen werden." };
		}
		let merged;
		try {
			merged = await mergeToWav(stored ? [stored, blob] : [blob]);
		}
		catch (err) {
			return { partId, ok: false, error: errorText(err) };
		}
		try {
			const saved = await store.replaceAudio(rec.id, merged.blob, `${rec.title || rec.id}.wav`, merged.seconds);
			return { partId, ok: true, ...saved };
		}
		catch (err) {
			return { partId, ok: false, error: `Speichern fehlgeschlagen: ${errorText(err)}` };
		}
	}

	function queueAudio(partId: string, blob: Blob): void {
		pendingMerges.value += 1;
		setPart(partId, { audio: "appending", audioError: null });
		mergeChain = mergeChain
			.then(async () => {
				const result = await appendAudio(partId, blob);
				if (result.ok) {
					setPart(partId, { audio: "saved", audioError: null });
					dropSession(partId);
					settle(partId);
				}
				else {
					setPart(partId, { audio: "failed", audioError: result.error });
				}
				// The page updates the recording's audio file here, before the next append reads it.
				hooks.onAudio(result);
			})
			// Nothing that throws here may break the chain: later parts still get appended.
			.catch((err: unknown) => {
				const message = errorText(err);
				if (parts.value.find(p => p.id === partId)?.audio === "appending") {
					setPart(partId, { audio: "failed", audioError: message });
				}
				try {
					hooks.onAudio({ partId, ok: false, error: message });
				}
				catch {
					// The part's audio state above still reports it.
				}
			})
			.finally(() => {
				pendingMerges.value -= 1;
			});
	}

	async function start(partId: string): Promise<boolean> {
		if (recording.value) return false;
		micAttempt += 1;
		const current = micAttempt;
		startError.value = null;
		micPart = partId;
		recording.value = true;
		let started: boolean;
		try {
			// False when a cancel (its recorder.stop()) or a newer start came while the
			// microphone opened: the recorder then released that stream itself.
			started = await recorder.start();
		}
		catch (err) {
			if (current === micAttempt) {
				recording.value = false;
				micPart = null;
				startError.value = errorText(err);
			}
			return false;
		}
		if (!started) {
			if (current === micAttempt) {
				recording.value = false;
				micPart = null;
			}
			return false;
		}
		// The recorder runs for this start (nothing came in between), so stopping it
		// here can only close this attempt's microphone.
		if (current !== micAttempt) {
			const session = recorder.sessionId.value;
			await recorder.stop();
			if (session) void discardUnfinished(session);
			return false;
		}
		return true;
	}

	function dropSession(partId: string): void {
		const session = sessions.get(partId);
		sessions.delete(partId);
		if (session) void discardUnfinished(session);
	}

	async function cancel(): Promise<void> {
		if (!recording.value) return;
		micAttempt += 1;
		recording.value = false;
		micPart = null;
		const session = recorder.sessionId.value;
		await recorder.stop();
		if (session) void discardUnfinished(session);
	}

	async function finish(): Promise<boolean> {
		const id = micPart;
		if (!recording.value || id === null) return false;
		micAttempt += 1;
		micPart = null;
		const session = recorder.sessionId.value;
		const blob = await recorder.stop();
		recording.value = false;
		if (blob.size === 0) {
			if (session) void discardUnfinished(session);
			startError.value = "Die Aufnahme ist leer.";
			return false;
		}
		if (session) sessions.set(id, session);
		blobs.set(id, blob);
		parts.value = [...parts.value, { id, status: "transcribing", error: null, audio: "appending", audioError: null }];
		queueAudio(id, blob);
		void runPart(id);
		return true;
	}

	function retry(partId: string): void {
		const part = parts.value.find(p => p.id === partId);
		if (part?.status !== "error") return;
		void runPart(partId);
	}

	function retryAudio(partId: string): void {
		const blob = blobs.get(partId);
		if (parts.value.find(p => p.id === partId)?.audio !== "failed" || !blob) return;
		queueAudio(partId, blob);
	}

	function discard(partId: string): void {
		const part = parts.value.find(p => p.id === partId);
		if (part?.status !== "error" && part?.audio !== "failed") return;
		forget(partId);
		dropSession(partId);
	}

	onScopeDispose(() => {
		disposed = true;
		for (const wake of [...wakers]) wake();
		if (recording.value) void cancel();
		if (typeof window !== "undefined") window.removeEventListener("beforeunload", onBeforeUnload);
	});

	return {
		recorder,
		recording,
		startError,
		parts,
		transcribing,
		merging,
		busy,
		failed,
		start,
		cancel,
		finish,
		retry,
		retryAudio,
		discard,
	};
}
