import { ref } from "vue";
import type { Ref } from "vue";
import type { Recording } from "~/types/directus";
import { humanizeError } from "#shared/errors";
import { normalizeAudio, probeDuration } from "~/utils/audioFile";

interface SaveArgs {
	blob: Blob;
	title: string;
	language: string;
	profile: string;
	transcribe: boolean;
	/** Known length in seconds (recorder); 0/omitted → read from the audio itself. */
	duration_seconds: number;
	/** Original filename of an uploaded/shared file (used for MIME + extension). */
	sourceName?: string;
}

export function useUpload() {
	const uploading: Ref<boolean> = ref(false);
	const error: Ref<string | null> = ref(null);

	async function saveRecording(args: SaveArgs): Promise<Recording> {
		uploading.value = true;
		error.value = null;
		try {
			// Uploads and shares arrive without a length and often with a missing or
			// generic MIME type: read the real duration and store a playable type.
			const { blob, filename } = normalizeAudio(args.blob, args.title, args.sourceName);
			const duration = args.duration_seconds > 0 ? args.duration_seconds : await probeDuration(blob);
			const created = await useDataStore().createRecording({
				title: args.title,
				language: args.language,
				profile: args.profile,
				source: "batch",
				status: args.transcribe ? "pending" : "done",
				duration_seconds: duration,
			}, blob, filename);

			return created;
		}
		catch (err) {
			error.value = humanizeError(err, "Upload fehlgeschlagen.");
			throw err;
		}
		finally {
			uploading.value = false;
		}
	}

	return {
		uploading,
		error,
		saveRecording,
	};
}
