import { computed, onScopeDispose, ref, shallowRef, type ComputedRef, type Ref } from "vue";
import { HubError, type HubJob } from "~/composables/useHub";
import { deleteJobQuietly, isTransient, jobFailureMessage, jobReadErrorMessage } from "~/data/localProcessor";
import { mergeToWav } from "~/utils/audioMerge";
import type { Recording } from "~/types/directus";

export type ContinuePhase = "idle" | "recording" | "transcribing" | "merging" | "error";

export interface ContinueResult {
	/** Trimmed transcript of the new part only. */
	text: string;
	/** New file id after the merge, null if the merge failed. */
	audio_file: string | null;
	/** Old + new when merged, else the old duration. */
	duration_seconds: number;
	audioMerged: boolean;
	/** German reason, set when the text succeeded but the merge failed. */
	mergeError: string | null;
}

export interface ContinueRecording {
	/** Own recorder instance, separate from page dictation. */
	recorder: ReturnType<typeof useRecorder>;
	phase: Ref<ContinuePhase>;
	/** German message while `phase` is `error`. */
	error: Ref<string | null>;
	/** A recorded part whose transcription failed is kept for `retry`. */
	hasRetainedPart: ComputedRef<boolean>;
	start(): Promise<void>;
	cancel(): Promise<void>;
	finish(recording: Recording): Promise<ContinueResult | null>;
	retry(recording: Recording): Promise<ContinueResult | null>;
	discard(): void;
}

const POLL_MS = 2000;
const NO_TOKEN = "Kein Hub-Token – in Einstellungen → Verbindung eintragen, dann erneut versuchen.";

function errorText(err: unknown): string {
	return err instanceof Error ? err.message : String(err);
}

/** Upload name for a recorded part, extension from its MIME type. */
function partFilename(part: Blob, recording: Recording): string {
	const ext = part.type.includes("ogg") ? "ogg" : part.type.includes("mp4") ? "m4a" : "webm";
	return `${recording.title || recording.id}-weiter.${ext}`;
}

function onBeforeUnload(event: Event): void {
	event.preventDefault();
}

/**
 * Continue recording: records a new part with its own recorder, transcribes it
 * on its own as a hub job (upload → job → poll → result) and merges the stored
 * audio with the part into one 16 kHz mono WAV that replaces the recording's
 * file. A part whose transcription failed is kept for `retry`; a failed merge
 * still returns the text. The page has to stay open until `finish` resolves.
 */
export function useContinueRecording(): ContinueRecording {
	const recorder = useRecorder();
	const hub = useHub();
	const store = useDataStore();

	const phase: Ref<ContinuePhase> = ref("idle");
	const error: Ref<string | null> = ref(null);
	const part = shallowRef<Blob | null>(null);
	const hasRetainedPart: ComputedRef<boolean> = computed(() => part.value !== null);

	// Bumped by cancel/discard/dispose: a running attempt sees it is stale and stops.
	let attempt = 0;
	let wake: (() => void) | null = null;
	let guarded = false;

	function setPhase(next: ContinuePhase): void {
		phase.value = next;
		const guard = next === "recording" || next === "transcribing" || next === "merging";
		if (guard === guarded || typeof window === "undefined") return;
		if (guard) window.addEventListener("beforeunload", onBeforeUnload);
		else window.removeEventListener("beforeunload", onBeforeUnload);
		guarded = guard;
	}

	function fail(message: string): void {
		error.value = message;
		setPhase("error");
	}

	// Executor form instead of Promise.withResolvers: iOS Safari < 17.4.
	function sleep(ms: number): Promise<void> {
		return new Promise((resolve) => {
			const timer = setTimeout(() => {
				wake = null;
				resolve();
			}, ms);
			wake = () => {
				clearTimeout(timer);
				wake = null;
				resolve();
			};
		});
	}

	function abortAttempt(): void {
		attempt += 1;
		wake?.();
	}

	/** Transcript of `blob` via a hub job; null when the attempt was cancelled. */
	async function transcribe(blob: Blob, recording: Recording, current: number): Promise<string | null> {
		const stale = () => current !== attempt;
		// Fresh key per attempt: a retry after a failed job must not get the failed job back.
		const key = `continue:recordings:${recording.id}:${crypto.randomUUID()}`;
		const uploadId = await hub.upload(blob, partFilename(blob, recording));
		if (stale()) return null;
		const jobId = await hub.createJob(
			{ kind: "audio", upload_id: uploadId, language: recording.language, profile: recording.profile },
			key,
		);
		for (;;) {
			if (!stale()) await sleep(POLL_MS);
			if (stale()) {
				await deleteJobQuietly(hub, jobId);
				return null;
			}
			let job: HubJob;
			try {
				job = await hub.getJob(jobId);
			}
			catch (err) {
				if (isTransient(err)) continue;
				throw new Error(jobReadErrorMessage(err, "job") ?? errorText(err), { cause: err });
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
				throw new Error(jobReadErrorMessage(err, "result") ?? errorText(err), { cause: err });
			}
			await deleteJobQuietly(hub, jobId);
			return markdown.trim();
		}
	}

	async function loadStoredAudio(recording: Recording): Promise<Blob | null> {
		const url = await store.audioUrl(recording);
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

	/** Merges stored audio + part and stores it; returns a German reason on failure. */
	async function mergeAudio(recording: Recording, blob: Blob): Promise<{ audio_file: string; duration_seconds: number } | string> {
		let parts: Blob[];
		try {
			const stored = await loadStoredAudio(recording);
			parts = stored ? [stored, blob] : [blob];
		}
		catch {
			return "Bisheriges Audio konnte nicht geladen werden.";
		}
		let merged;
		try {
			merged = await mergeToWav(parts);
		}
		catch (err) {
			return errorText(err);
		}
		try {
			return await store.replaceAudio(recording.id, merged.blob, `${recording.title || recording.id}.wav`, merged.seconds);
		}
		catch (err) {
			return `Speichern fehlgeschlagen: ${errorText(err)}`;
		}
	}

	async function processPart(recording: Recording): Promise<ContinueResult | null> {
		const blob = part.value;
		if (!blob) return null;
		attempt += 1;
		const current = attempt;
		error.value = null;
		if (!hub.hasToken()) {
			fail(NO_TOKEN);
			return null;
		}
		setPhase("transcribing");
		let text: string | null;
		try {
			text = await transcribe(blob, recording, current);
		}
		catch (err) {
			if (current === attempt) {
				fail(err instanceof HubError ? `Transkription fehlgeschlagen: ${err.message}` : errorText(err));
			}
			return null;
		}
		if (text === null) return null;

		setPhase("merging");
		const merged = await mergeAudio(recording, blob);
		part.value = null;
		setPhase("idle");
		const oldSeconds = recording.duration_seconds ?? 0;
		return typeof merged === "string"
			? { text, audio_file: null, duration_seconds: oldSeconds, audioMerged: false, mergeError: merged }
			: { text, audio_file: merged.audio_file, duration_seconds: merged.duration_seconds, audioMerged: true, mergeError: null };
	}

	async function start(): Promise<void> {
		const ready = phase.value === "idle" || (phase.value === "error" && part.value === null);
		if (!ready) return;
		error.value = null;
		setPhase("recording");
		try {
			await recorder.start();
		}
		catch (err) {
			fail(errorText(err));
		}
	}

	async function cancel(): Promise<void> {
		if (phase.value === "recording") {
			await recorder.stop();
			setPhase("idle");
			return;
		}
		if (phase.value === "transcribing") {
			abortAttempt();
			part.value = null;
			error.value = null;
			setPhase("idle");
		}
	}

	async function finish(recording: Recording): Promise<ContinueResult | null> {
		if (phase.value !== "recording") return null;
		const blob = await recorder.stop();
		if (blob.size === 0) {
			fail("Die Aufnahme ist leer.");
			return null;
		}
		part.value = blob;
		return processPart(recording);
	}

	async function retry(recording: Recording): Promise<ContinueResult | null> {
		if (phase.value !== "error") return null;
		return processPart(recording);
	}

	function discard(): void {
		if (phase.value !== "error") return;
		part.value = null;
		error.value = null;
		setPhase("idle");
	}

	onScopeDispose(() => {
		abortAttempt();
		setPhase("idle");
	});

	return {
		recorder,
		phase,
		error,
		hasRetainedPart,
		start,
		cancel,
		finish,
		retry,
		discard,
	};
}
