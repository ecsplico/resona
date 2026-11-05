import { ref } from "vue";
import type { Ref } from "vue";
import type { Recording } from "~/types/directus";
import { humanizeError } from "#shared/errors";

interface SaveArgs {
	blob: Blob;
	title: string;
	language: string;
	profile: string;
	transcribe: boolean;
	duration_seconds: number;
}

export function useUpload() {
	const uploading: Ref<boolean> = ref(false);
	const error: Ref<string | null> = ref(null);

	async function saveRecording(args: SaveArgs): Promise<Recording> {
		uploading.value = true;
		error.value = null;
		try {
			const created = await useDataStore().createRecording({
				title: args.title,
				language: args.language,
				profile: args.profile,
				source: "batch",
				status: args.transcribe ? "pending" : "done",
				duration_seconds: args.duration_seconds,
			}, args.blob, `${args.title || "recording"}.webm`);

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
