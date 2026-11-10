import { createStore, del, get, promisifyRequest, set, values } from "idb-keyval";
import type { UseStore } from "idb-keyval";
import type { Provenance, Recording, ResonaDocument, Transcript } from "~/types/directus";
import type { DataStore } from "~/data/types";

// Browser-local backend for NUXT_PUBLIC_DATA_MODE=standalone. idb-keyval can
// only hold one object store per database, so every collection gets its own
// `resona-local-*` database with a single `items` store.

// Recordings carry date_updated locally even though the Directus type omits it.
type LocalRecording = Recording & { date_updated?: string };

// Files are persisted as raw bytes + type + name (same rehydration approach as
// useOfflineQueue) so they survive structured cloning into IndexedDB.
interface StoredFile {
	bytes: ArrayBuffer;
	type: string;
	name: string;
}

interface Stores {
	recordings: UseStore;
	transcripts: UseStore;
	documents: UseStore;
	files: UseStore;
	corrections: UseStore;
}

let stores: Stores | null = null;

function db(): Stores {
	if (!stores) {
		stores = {
			recordings: createStore("resona-local-recordings", "items"),
			transcripts: createStore("resona-local-transcripts", "items"),
			documents: createStore("resona-local-documents", "items"),
			files: createStore("resona-local-files", "items"),
			corrections: createStore("resona-local-corrections", "items"),
		};
	}
	return stores;
}

let lastTimestamp = 0;

// Strictly increasing ISO timestamps: two rows created within the same
// millisecond still sort deterministically by date_created.
function now(): string {
	const t = Math.max(Date.now(), lastTimestamp + 1);
	lastTimestamp = t;
	return new Date(t).toISOString();
}

function byCreatedAsc(a: { date_created?: string }, b: { date_created?: string }): number {
	const x = a.date_created ?? "";
	const y = b.date_created ?? "";
	return x < y ? -1 : x > y ? 1 : 0;
}

function titleMatches(title: string, search: string | undefined): boolean {
	if (typeof search !== "string" || search.length === 0) return true;
	return title.toLowerCase().includes(search.toLowerCase());
}

async function storeFile(blob: Blob, name: string): Promise<string> {
	const id = crypto.randomUUID();
	const file: StoredFile = { bytes: await blob.arrayBuffer(), type: blob.type, name };
	await set(id, file, db().files);
	return id;
}

async function deleteFile(fileId: string | undefined): Promise<void> {
	if (fileId) await del(fileId, db().files);
}

// Read-modify-write inside one readwrite transaction, so a processor patch and
// a UI patch of the same row cannot overwrite each other. A missing row is not
// written: it throws `notFound`, or resolves silently when `notFound` is null.
// A function patch sees the stored row; returning null writes nothing.
// (Executor form instead of Promise.withResolvers: iOS Safari < 17.4.)
async function patchRow<T extends { id: string; date_updated?: string }>(
	store: UseStore,
	id: string,
	patch: Partial<T> | ((row: T) => Partial<T> | null),
	notFound: string | null,
): Promise<void> {
	const found = await store<boolean>("readwrite", os => new Promise<boolean>((resolve, reject) => {
		const req = os.get(id);
		req.onerror = () => reject(req.error);
		req.onsuccess = () => {
			const row = req.result as T | undefined;
			if (!row) {
				resolve(false);
				return;
			}
			const changes = typeof patch === "function" ? patch(row) : patch;
			if (changes === null) {
				resolve(true);
				return;
			}
			os.put({ ...row, ...changes, id: row.id, date_updated: now() }, id);
			resolve(promisifyRequest(os.transaction).then(() => true));
		};
	}));
	if (!found && notFound !== null) throw new Error(notFound);
}

async function transcriptsOf(recordingId: string): Promise<Transcript[]> {
	const all = await values<Transcript>(db().transcripts);
	return all.filter(t => t.recording === recordingId).sort(byCreatedAsc);
}

/** Rows of a collection with the given status, oldest first (processor queue order). */
export function listByStatus(collection: "recordings", status: Recording["status"]): Promise<Recording[]>;
export function listByStatus(collection: "documents", status: ResonaDocument["status"]): Promise<ResonaDocument[]>;
export function listByStatus(collection: "recordings" | "documents", status: Recording["status"] | ResonaDocument["status"]): Promise<(Recording | ResonaDocument)[]>;
export async function listByStatus(collection: "recordings" | "documents", status: Recording["status"] | ResonaDocument["status"]): Promise<(Recording | ResonaDocument)[]> {
	const rows = await values<Recording | ResonaDocument>(db()[collection]);
	return rows.filter(r => r.status === status).sort(byCreatedAsc);
}

/** The stored file as a File (name and MIME type preserved), or null when absent. */
export async function readFile(fileId: string): Promise<Blob | null> {
	const file = await get<StoredFile>(fileId, db().files);
	if (!file) return null;
	return new File([file.bytes], file.name, { type: file.type });
}

/**
 * Replace an automatic title, only while the stored row still has `title_auto`
 * (checked in the same transaction, so a concurrent rename always wins).
 */
export async function setAutoTitleInternal(id: string, title: string): Promise<void> {
	await patchRow<LocalRecording>(db().recordings, id, row => (row.title_auto ? { title, title_auto: false } : null), null);
}

/** Processor-side patch of any recording field; a no-op when the recording was deleted meanwhile. */
export async function patchRecordingInternal(id: string, patch: Partial<Recording>): Promise<void> {
	await patchRow<LocalRecording>(db().recordings, id, patch, null);
}

/** Processor-side patch of any document field; a no-op when the document was deleted meanwhile. */
export async function patchDocumentInternal(id: string, patch: Partial<ResonaDocument>): Promise<void> {
	await patchRow<ResonaDocument>(db().documents, id, patch, null);
}

/** Transcripts whose re-processing ("Nachbearbeiten") is in `status`, oldest first. */
export async function listTranscriptsByPostprocess(status: "pending" | "processing"): Promise<Transcript[]> {
	const rows = await values<Transcript>(db().transcripts);
	return rows.filter(t => t.postprocess_status === status).sort(byCreatedAsc);
}

/** Processor-side patch of any transcript field; a no-op when the transcript was deleted meanwhile. */
export async function patchTranscriptInternal(id: string, patch: Partial<Transcript>): Promise<void> {
	await patchRow<Transcript>(db().transcripts, id, patch, null);
}

/**
 * Creates the recording's transcript, or replaces the content of the existing
 * one in place (id and date_created kept). A new transcription is raw again: a
 * previous profile run (`raw_text`, `applied_profile`, `postprocess_*`) is dropped,
 * and the hub text job it still had in flight is returned so the caller can delete it.
 * A no-op when the recording was deleted meanwhile, so no orphan transcript is left behind.
 */
export async function upsertTranscript(
	recordingId: string,
	t: { text: string; segments?: unknown; structured?: unknown; engine?: string | null; provenance?: Provenance | null },
): Promise<{ supersededJob: number | null }> {
	const recording = await get<LocalRecording>(recordingId, db().recordings);
	if (!recording) return { supersededJob: null };
	const [existing] = await transcriptsOf(recordingId);
	const ts = now();
	const row: Transcript = {
		id: existing?.id ?? crypto.randomUUID(),
		recording: recordingId,
		text: t.text,
		segments: (t.segments ?? null) as Transcript["segments"],
		structured: (t.structured ?? null) as Transcript["structured"],
		date_created: existing?.date_created ?? ts,
		date_updated: ts,
	};
	if (t.engine) row.engine = t.engine;
	if (t.provenance) row.provenance = t.provenance;
	await set(row.id, row, db().transcripts);
	return { supersededJob: existing?.postprocess_job_id ?? null };
}

export function createLocalStore(): DataStore {
	return {
		mode: "standalone",

		async listRecordings(opts) {
			const rows = await values<LocalRecording>(db().recordings);
			return rows
				.filter(r => titleMatches(r.title, opts.search))
				.filter(r => !opts.since || (r.date_created ?? "") >= opts.since)
				.sort((a, b) => byCreatedAsc(b, a))
				.slice(0, 100);
		},

		async getRecording(id) {
			const row = await get<LocalRecording>(id, db().recordings);
			if (!row) throw new Error("Aufnahme nicht gefunden");
			return row;
		},

		async getTranscript(recordingId) {
			const [first] = await transcriptsOf(recordingId);
			return first ?? null;
		},

		async createRecording(input, audio, filename) {
			const ts = now();
			const row: LocalRecording = {
				id: crypto.randomUUID(),
				title: input.title,
				title_auto: input.title_auto ?? false,
				language: input.language,
				profile: input.profile,
				source: input.source,
				status: input.status,
				duration_seconds: input.duration_seconds,
				error_message: null,
				hub_job_id: null,
				hub_job_key: null,
				date_created: ts,
				date_updated: ts,
			};
			if (audio) row.audio_file = await storeFile(audio, filename);
			await set(row.id, row, db().recordings);
			return row;
		},

		async updateRecording(id, patch) {
			await patchRow<LocalRecording>(db().recordings, id, patch, "Aufnahme nicht gefunden");
		},

		async replaceAudio(id, audio, filename, durationSeconds) {
			const row = await get<LocalRecording>(id, db().recordings);
			if (!row) throw new Error("Aufnahme nicht gefunden");
			const fileId = await storeFile(audio, filename);
			try {
				await patchRow<LocalRecording>(db().recordings, id, { audio_file: fileId, duration_seconds: durationSeconds }, "Aufnahme nicht gefunden");
			}
			catch (err) {
				await deleteFile(fileId);
				throw err;
			}
			await deleteFile(row.audio_file);
			return { audio_file: fileId, duration_seconds: durationSeconds };
		},

		async deleteRecording(id) {
			const row = await get<LocalRecording>(id, db().recordings);
			for (const t of await transcriptsOf(id)) {
				await del(t.id, db().transcripts);
			}
			await deleteFile(row?.audio_file);
			await del(id, db().recordings);
		},

		async createTranscript(recordingId, input) {
			const ts = now();
			const row: Transcript = {
				id: crypto.randomUUID(),
				recording: recordingId,
				text: input.text,
				date_created: ts,
				date_updated: ts,
			};
			if (input.engine) row.engine = input.engine;
			await set(row.id, row, db().transcripts);
			return row;
		},

		async updateTranscript(id, patch) {
			await patchRow<Transcript>(db().transcripts, id, patch, "Transkript nicht gefunden");
		},

		async recordCorrection(input) {
			await set(crypto.randomUUID(), { ...input, date_created: now() }, db().corrections);
		},

		async audioUrl(recording) {
			if (!recording.audio_file) return null;
			const blob = await readFile(recording.audio_file);
			return blob ? URL.createObjectURL(blob) : null;
		},

		async listDocuments(opts) {
			const rows = await values<ResonaDocument>(db().documents);
			return rows
				.filter(d => titleMatches(d.title, opts.search))
				.sort((a, b) => byCreatedAsc(b, a))
				.slice(0, 100);
		},

		async getDocument(id) {
			const row = await get<ResonaDocument>(id, db().documents);
			if (!row) throw new Error("Dokument nicht gefunden");
			return row;
		},

		async createDocument(input, file, filename) {
			const fileId = await storeFile(file, filename);
			const ts = now();
			const row: ResonaDocument = {
				id: crypto.randomUUID(),
				title: input.title,
				file: fileId,
				status: "pending",
				error_message: null,
				markdown: null,
				pages: null,
				hub_job_id: null,
				hub_job_key: null,
				date_created: ts,
				date_updated: ts,
			};
			await set(row.id, row, db().documents);
			return row;
		},

		async updateDocument(id, patch) {
			await patchRow<ResonaDocument>(db().documents, id, patch, "Dokument nicht gefunden");
		},

		async deleteDocument(id) {
			const row = await get<ResonaDocument>(id, db().documents);
			await deleteFile(row?.file);
			await del(id, db().documents);
		},
	};
}
