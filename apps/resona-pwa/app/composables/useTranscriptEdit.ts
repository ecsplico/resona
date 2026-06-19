import { updateItem } from "@directus/sdk";
import { ref } from "vue";
import type { Ref } from "vue";

export function useTranscriptEdit(initialText = "") {
	const text: Ref<string> = ref(initialText);
	const saving: Ref<boolean> = ref(false);
	const error: Ref<string | null> = ref(null);

	// The last value known to be persisted. A failed save reverts to this,
	// not to the original initial value across multiple saves.
	let committed = initialText;

	async function saveEdit(transcriptId: string, newText: string): Promise<void> {
		const previous = committed;
		saving.value = true;
		error.value = null;
		text.value = newText; // optimistic
		try {
			const { client } = useDirectus();
			await client.request(updateItem("transcripts", transcriptId, { text: newText }));
			committed = newText;
		}
		catch (err) {
			text.value = previous;
			committed = previous;
			error.value = err instanceof Error ? err.message : "Speichern fehlgeschlagen";
		}
		finally {
			saving.value = false;
		}
	}

	return {
		text,
		saving,
		error,
		saveEdit,
	};
}
