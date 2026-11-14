import { createItem, deleteItem, readItem, readItems, updateItem, uploadFiles } from "@directus/sdk";
import type { Query } from "@directus/sdk";
import type { Recording, RecordingPart, ResonaDocument, Schema, Transcript } from "~/types/directus";
import type { DataStore } from "~/data/types";

async function uploadFile(file: Blob, filename: string): Promise<string> {
	const { client } = useDirectus();
	const form = new FormData();
	// Wrap in a File so the filename survives every FormData implementation
	// (happy-dom drops the third append() argument for plain Blobs).
	form.append("file", new File([file], filename, { type: file.type }));
	const uploaded = await client.request(uploadFiles(form)) as { id: string };
	return uploaded.id;
}

export function createDirectusStore(): DataStore {
	return {
		mode: "directus",

		async listRecordings(opts) {
			const filter: Record<string, unknown> = {};
			if (typeof opts.search === "string" && opts.search.length > 0) {
				filter.title = { _icontains: opts.search };
			}
			if (opts.since) {
				filter.date_created = { _gte: opts.since };
			}
			const query: Query<Schema, Recording> = {
				sort: ["-date_created"],
				limit: 100,
			};
			if (Object.keys(filter).length > 0) {
				query.filter = filter as Query<Schema, Recording>["filter"];
			}
			const { client } = useDirectus();
			return await client.request(readItems("recordings", query)) as Recording[];
		},

		async getRecording(id) {
			const { client } = useDirectus();
			return await client.request(readItem("recordings", id)) as Recording;
		},

		async getTranscript(recordingId) {
			const { client } = useDirectus();
			const rows = await client.request(readItems("transcripts", {
				filter: { recording: { _eq: recordingId } },
				limit: 1,
			})) as Transcript[];
			return rows[0] ?? null;
		},

		async createRecording(input, audio, filename) {
			const fileId = audio ? await uploadFile(audio, filename) : null;
			const { client } = useDirectus();
			return await client.request(createItem("recordings", {
				title: input.title,
				title_auto: input.title_auto ?? false,
				audio_file: fileId ?? undefined,
				language: input.language,
				profile: input.profile,
				source: input.source,
				status: input.status,
				duration_seconds: input.duration_seconds,
			})) as Recording;
		},

		async updateRecording(id, patch) {
			const { client } = useDirectus();
			await client.request(updateItem("recordings", id, patch));
		},

		// The previous file stays in directus_files: the `user` role has no delete
		// permission there (same as deleteRecording).
		async replaceAudio(id, audio, filename, durationSeconds) {
			const fileId = await uploadFile(audio, filename);
			const { client } = useDirectus();
			await client.request(updateItem("recordings", id, { audio_file: fileId, duration_seconds: durationSeconds }));
			return { audio_file: fileId, duration_seconds: durationSeconds };
		},

		async deleteRecording(id) {
			const { client } = useDirectus();
			await client.request(deleteItem("recordings", id));
		},

		async createTranscript(recordingId, input) {
			const { client } = useDirectus();
			const payload: Partial<Transcript> = { recording: recordingId, text: input.text };
			if (input.engine) payload.engine = input.engine;
			return await client.request(createItem("transcripts", payload)) as Transcript;
		},

		async updateTranscript(id, patch) {
			const { client } = useDirectus();
			await client.request(updateItem("transcripts", id, patch));
		},

		async recordCorrection(input) {
			const { client } = useDirectus();
			await client.request(createItem("transcript_corrections", input));
		},

		async audioUrl(recording) {
			return recording.audio_file ? useDirectus().assetUrl(recording.audio_file) : null;
		},

		async listDocuments(opts) {
			const query: Query<Schema, ResonaDocument> = {
				sort: ["-date_created"],
				limit: 100,
			};
			if (typeof opts.search === "string" && opts.search.length > 0) {
				query.filter = { title: { _icontains: opts.search } } as Query<Schema, ResonaDocument>["filter"];
			}
			const { client } = useDirectus();
			return await client.request(readItems("documents", query)) as ResonaDocument[];
		},

		async getDocument(id) {
			const { client } = useDirectus();
			return await client.request(readItem("documents", id)) as ResonaDocument;
		},

		async createDocument(input, file, filename) {
			const fileId = await uploadFile(file, filename);
			const { client } = useDirectus();
			return await client.request(createItem("documents", {
				title: input.title,
				file: fileId,
				status: "pending",
			})) as ResonaDocument;
		},

		async updateDocument(id, patch) {
			const { client } = useDirectus();
			await client.request(updateItem("documents", id, patch));
		},

		async deleteDocument(id) {
			const { client } = useDirectus();
			await client.request(deleteItem("documents", id));
		},

		async listParts(recordingId) {
			const { client } = useDirectus();
			return await client.request(readItems("recording_parts", {
				filter: { recording: { _eq: recordingId } },
				sort: ["seq"],
				limit: -1,
			})) as RecordingPart[];
		},

		// `status` is sent although it is the default: the `user` policy validates it on create.
		async createPart(input, audio, filename) {
			const fileId = await uploadFile(audio, filename);
			const { client } = useDirectus();
			return await client.request(createItem("recording_parts", { ...input, audio: fileId, status: "pending" })) as RecordingPart;
		},

		async updatePart(id, patch) {
			const { client } = useDirectus();
			await client.request(updateItem("recording_parts", id, patch));
		},

		async partAudio(part) {
			if (!part.audio) return null;
			const resp = await fetch(useDirectus().assetUrl(part.audio));
			if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
			return await resp.blob();
		},
	};
}
