import { ref } from "vue";
import type { Ref } from "vue";
import type { Transcript } from "~/types/directus";
import { humanizeError } from "#shared/errors";
import { diffEdits } from "~/utils/correction";

/** Where an edit belongs, so a saved change can be stored as an engine correction. */
export interface CorrectionContext {
	recording: string;
	engine?: string | null;
	language?: string | null;
}

export function useTranscriptEdit(initialText = "") {
	const text: Ref<string> = ref(initialText);
	// The last value known to be persisted; `text !== committed` means unsaved changes.
	const committed: Ref<string> = ref(initialText);
	const saving: Ref<boolean> = ref(false);
	const error: Ref<string | null> = ref(null);

	// Load a persisted text (e.g. the transcript fetched from the store) as both
	// the editor content and the committed baseline.
	function seed(value: string): void {
		text.value = value;
		committed.value = value;
		error.value = null;
	}

	// Shared write wrapper: apply `newText`, run the request, commit on success.
	// A failure keeps the text as it is (it may hold edits that exist nowhere
	// else) and only reports the error, so the user can save again. Returns the
	// request result (or null on failure) so callers can pick up e.g. a created row.
	async function persist<T>(newText: string, run: () => Promise<T>): Promise<T | null> {
		saving.value = true;
		error.value = null;
		text.value = newText;
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
			saving.value = false;
		}
	}

	// With `correction`, the saved change is also stored as a correction of the
	// engine's text (training data). That is best effort: a failure there never
	// fails the save.
	async function saveEdit(transcriptId: string, newText: string, correction?: CorrectionContext): Promise<void> {
		const before = committed.value;
		const saved = await persist(newText, () => useDataStore().updateTranscript(transcriptId, { text: newText }));
		if (saved === null && error.value) return;
		const edits = correction ? diffEdits(before, newText) : [];
		if (!correction || edits.length === 0) return;
		try {
			await useDataStore().recordCorrection({
				transcript: transcriptId,
				recording: correction.recording,
				before,
				after: newText,
				edits,
				engine: correction.engine ?? null,
				language: correction.language ?? null,
			});
		}
		catch (err) {
			console.warn("could not store transcript correction", err);
		}
	}

	// Create the transcript row for a recording that has none yet — a manual note
	// or dictation into an untranscribed recording. Returns the new row on
	// success, null on failure.
	async function createTranscript(recordingId: string, newText: string): Promise<Transcript | null> {
		return persist(newText, () => useDataStore().createTranscript(recordingId, { text: newText }));
	}

	return {
		text,
		committed,
		saving,
		error,
		seed,
		saveEdit,
		createTranscript,
	};
}
