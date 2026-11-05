import { ref } from "vue";
import type { Ref } from "vue";
import type { Transcript } from "~/types/directus";
import { humanizeError } from "#shared/errors";

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

	async function saveEdit(transcriptId: string, newText: string): Promise<void> {
		await persist(newText, () => useDataStore().updateTranscript(transcriptId, { text: newText }));
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
