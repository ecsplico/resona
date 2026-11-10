import type { CorrectionEdit, Recording, ResonaDocument, Transcript } from "~/types/directus";

/** `title_auto`: the title is a date placeholder the processor replaces with one derived from the transcript. */
export interface NewRecording { title: string; title_auto?: boolean; language: string; profile: string; source: "batch" | "live"; status: "pending" | "done"; duration_seconds: number }
/**
 * Transcript fields the user writes: the text, plus the "Nachbearbeiten" request and
 * undo (see `~/utils/postprocess`). The hub job fields (`postprocess_job_*`,
 * `postprocess_error`, `postprocess_queue_reason`) belong to the processor; in
 * Directus mode the `user` policy refuses them.
 */
export type TranscriptPatch = Partial<Pick<Transcript,
	"text" | "raw_text" | "applied_profile" | "processed_text" | "postprocess_status" | "postprocess_profile">>;
export interface RecordingPatch { title?: string; title_auto?: boolean; status?: Recording["status"]; error_message?: string | null; review_status?: "editing" | "ready" }
export interface DataStore {
	readonly mode: "directus" | "standalone";
	listRecordings(opts: { search?: string; since?: string }): Promise<Recording[]>;
	getRecording(id: string): Promise<Recording>;
	getTranscript(recordingId: string): Promise<Transcript | null>;
	createRecording(input: NewRecording, audio: Blob | null, filename: string): Promise<Recording>;
	updateRecording(id: string, patch: RecordingPatch): Promise<void>;
	/** Makes `audio` the recording's audio file and sets its duration (continue recording). */
	replaceAudio(id: string, audio: Blob, filename: string, durationSeconds: number): Promise<{ audio_file: string; duration_seconds: number }>;
	deleteRecording(id: string): Promise<void>;
	createTranscript(recordingId: string, input: { text: string; engine?: string }): Promise<Transcript>;
	updateTranscript(id: string, patch: TranscriptPatch): Promise<void>;
	/** Stores a user's edit of a transcript as a correction for later engine tuning. */
	recordCorrection(input: { transcript: string; recording: string; before: string; after: string; edits: CorrectionEdit[]; engine?: string | null; language?: string | null }): Promise<void>;
	audioUrl(recording: Recording): Promise<string | null>;
	listDocuments(opts: { search?: string }): Promise<ResonaDocument[]>;
	getDocument(id: string): Promise<ResonaDocument>;
	createDocument(input: { title: string }, file: Blob, filename: string): Promise<ResonaDocument>;
	updateDocument(id: string, patch: { status?: ResonaDocument["status"]; error_message?: string | null }): Promise<void>;
	deleteDocument(id: string): Promise<void>;
}
