import { uploadFiles, createItem } from "@directus/sdk";
import { ref } from "vue";
import type { Ref } from "vue";
import type { Recording } from "~/types/directus";

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

	async function uploadBlob(blob: Blob, name = "recording"): Promise<string> {
		uploading.value = true;
		error.value = null;
		try {
			const { client } = useDirectus();
			const form = new FormData();
			form.append("file", blob, `${name || "recording"}.webm`);
			const uploaded = await client.request(uploadFiles(form)) as { id: string };
			return uploaded.id;
		}
		catch (err) {
			error.value = err instanceof Error ? err.message : "Upload failed";
			throw err;
		}
		finally {
			uploading.value = false;
		}
	}

	async function saveRecording(args: SaveArgs): Promise<Recording> {
		uploading.value = true;
		error.value = null;
		try {
			const { client } = useDirectus();

			const form = new FormData();
			form.append("file", args.blob, `${args.title || "recording"}.webm`);
			const uploaded = await client.request(uploadFiles(form)) as { id: string };

			const created = await client.request(createItem("recordings", {
				title: args.title,
				audio_file: uploaded.id,
				language: args.language,
				profile: args.profile,
				source: "batch",
				status: args.transcribe ? "pending" : "done",
				duration_seconds: args.duration_seconds,
			})) as Recording;

			return created;
		}
		catch (err) {
			error.value = err instanceof Error ? err.message : "Upload failed";
			throw err;
		}
		finally {
			uploading.value = false;
		}
	}

	return {
		uploading,
		error,
		uploadBlob,
		saveRecording,
	};
}
