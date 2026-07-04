import { createItem, updateItem } from "@directus/sdk";
import { ref } from "vue";
import type { Ref } from "vue";
import type { Transcript } from "~/types/directus";
import { humanizeError } from "#shared/errors";

export function useTranscriptEdit(initialText = "") {
	const text: Ref<string> = ref(initialText);
	const saving: Ref<boolean> = ref(false);
	const error: Ref<string | null> = ref(null);

	// The last value known to be persisted. A failed save reverts to this,
	// not to the original initial value across multiple saves.
	let committed = initialText;

	// Shared optimistic-write wrapper: apply `newText`, run the request, commit on
	// success, roll back to the last committed value on failure. Returns the
	// request result (or null on failure) so callers can pick up e.g. a created row.
	async function persist<T>(newText: string, run: () => Promise<T>): Promise<T | null> {
		const previous = committed;
		saving.value = true;
		error.value = null;
		text.value = newText; // optimistic
		try {
			const result = await run();
			committed = newText;
			return result;
		}
		catch (err) {
			text.value = previous;
			committed = previous;
			error.value = humanizeError(err, "Speichern fehlgeschlagen.");
			return null;
		}
		finally {
			saving.value = false;
		}
	}

	async function saveEdit(transcriptId: string, newText: string): Promise<void> {
		const { client } = useDirectus();
		await persist(newText, () => client.request(updateItem("transcripts", transcriptId, { text: newText })));
	}

	// Create the transcript row for a recording that has none yet — a manual note
	// or dictation into an untranscribed recording. Returns the new row on
	// success, null on failure.
	async function createTranscript(recordingId: string, newText: string, engine?: string): Promise<Transcript | null> {
		const { client } = useDirectus();
		return persist(newText, () => client.request(createItem("transcripts", {
			recording: recordingId,
			text: newText,
			...(engine ? { engine } : {}),
		} as Partial<Transcript>)) as Promise<Transcript>);
	}

	return {
		text,
		saving,
		error,
		saveEdit,
		createTranscript,
	};
}
