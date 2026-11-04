import type { Recording, ResonaDocument, Transcript } from "~/types/directus";

export interface NewRecording { title: string; language: string; profile: string; source: "batch" | "live"; status: "pending" | "done"; duration_seconds: number }
export interface RecordingPatch { status?: Recording["status"]; error_message?: string | null; review_status?: "editing" | "ready" }
export interface DataStore {
	readonly mode: "directus" | "standalone";
	listRecordings(opts: { search?: string; since?: string }): Promise<Recording[]>;
	getRecording(id: string): Promise<Recording>;
	getTranscript(recordingId: string): Promise<Transcript | null>;
	createRecording(input: NewRecording, audio: Blob | null, filename: string): Promise<Recording>;
	updateRecording(id: string, patch: RecordingPatch): Promise<void>;
	deleteRecording(id: string): Promise<void>;
	createTranscript(recordingId: string, input: { text: string; engine?: string }): Promise<Transcript>;
	updateTranscript(id: string, patch: { text: string }): Promise<void>;
	audioUrl(recording: Recording): Promise<string | null>;
	listDocuments(opts: { search?: string }): Promise<ResonaDocument[]>;
	getDocument(id: string): Promise<ResonaDocument>;
	createDocument(input: { title: string }, file: Blob, filename: string): Promise<ResonaDocument>;
	updateDocument(id: string, patch: { status?: ResonaDocument["status"]; error_message?: string | null }): Promise<void>;
	deleteDocument(id: string): Promise<void>;
}
