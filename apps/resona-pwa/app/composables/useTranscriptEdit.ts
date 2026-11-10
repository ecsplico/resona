import { computed, ref } from "vue";
import type { ComputedRef, Ref } from "vue";
import type { Transcript } from "~/types/directus";
import type { TranscriptPatch } from "~/data/types";
import { humanizeError } from "#shared/errors";
import { diffEdits } from "~/utils/correction";

/** Where an edit belongs, so a saved change can be stored as an engine correction. */
export interface CorrectionContext {
	recording: string;
	engine?: string | null;
	language?: string | null;
}

// After the next paint (or a plain task where frames do not run, e.g. a hidden tab),
// so CPU work queued behind a write never delays showing its result.
// (Executor form instead of Promise.withResolvers: iOS Safari < 17.4.)
function afterPaint(): Promise<void> {
	return new Promise((resolve) => {
		const hidden = typeof document !== "undefined" && document.hidden;
		if (typeof requestAnimationFrame === "function" && !hidden) {
			requestAnimationFrame(() => setTimeout(resolve, 0));
		}
		else {
			setTimeout(resolve, 0);
		}
	});
}

export function useTranscriptEdit(initialText = "") {
	// The editor content. Writes never change it: it is the user's, and may already
	// hold more (typing, a dictated part) than the text being written.
	const text: Ref<string> = ref(initialText);
	// The last value known to be persisted.
	const committed: Ref<string> = ref(initialText);
	const error: Ref<string | null> = ref(null);
	// Writes queued or running; `saving` turns true the moment one is asked for.
	const pending = ref(0);
	const saving: ComputedRef<boolean> = computed(() => pending.value > 0);
	let tail: Promise<unknown> = Promise.resolve();

	// Load a persisted text (e.g. the transcript fetched from the store) as both
	// the editor content and the committed baseline.
	function seed(value: string): void {
		text.value = value;
		committed.value = value;
		error.value = null;
	}

	/**
	 * Runs `task` once every write queued before it has settled, so writes reach
	 * the store one at a time and each can build on the committed text of the one
	 * before. `saving` is true from this call until the task settles.
	 */
	function enqueue<T>(task: () => Promise<T>): Promise<T> {
		pending.value += 1;
		const run = tail.then(task).finally(() => {
			pending.value -= 1;
		});
		tail = run.catch(() => undefined);
		return run;
	}

	// Shared write wrapper: run the request, commit `newText` on success. A failure
	// only reports the error (the editor keeps everything), so the user can save
	// again. Returns the request result, or null on failure.
	async function persist<T>(newText: string, run: () => Promise<T>): Promise<T | null> {
		pending.value += 1;
		error.value = null;
		try {
			const result = await run();
			committed.value = newText;
			return result;
		}
		catch (err) {
			error.value = humanizeError(err, "Speichern fehlgeschlagen.");
			return null;
		}
		finally {
			pending.value -= 1;
		}
	}

	// Best effort, after the save has shown: the word diff is quadratic in the
	// changed span and a failure here never fails the save.
	async function recordCorrection(transcriptId: string, before: string, after: string, context: CorrectionContext): Promise<void> {
		const store = useDataStore();
		await afterPaint();
		const edits = diffEdits(before, after);
		if (edits.length === 0) return;
		try {
			await store.recordCorrection({
				transcript: transcriptId,
				recording: context.recording,
				before,
				after,
				edits,
				engine: context.engine ?? null,
				language: context.language ?? null,
			});
		}
		catch (err) {
			console.warn("could not store transcript correction", err);
		}
	}

	/**
	 * Writes `newText` (plus `extra` fields in the same update); true on success.
	 * With `correction`, the change against the committed text is also stored as a
	 * correction of the engine's text (training data), in the background.
	 */
	async function saveEdit(
		transcriptId: string,
		newText: string,
		options: { correction?: CorrectionContext; extra?: TranscriptPatch } = {},
	): Promise<boolean> {
		const before = committed.value;
		const store = useDataStore();
		const saved = await persist(newText, async () => {
			await store.updateTranscript(transcriptId, { ...options.extra, text: newText });
			return true;
		});
		if (saved === null) return false;
		if (options.correction) void recordCorrection(transcriptId, before, newText, options.correction);
		return true;
	}

	// Create the transcript row for a recording that has none yet — a manual note
	// or dictation into an untranscribed recording. Returns the new row on
	// success, null on failure.
	async function createTranscript(recordingId: string, newText: string): Promise<Transcript | null> {
		const store = useDataStore();
		return persist(newText, () => store.createTranscript(recordingId, { text: newText }));
	}

	return {
		text,
		committed,
		saving,
		error,
		seed,
		enqueue,
		saveEdit,
		createTranscript,
	};
}
