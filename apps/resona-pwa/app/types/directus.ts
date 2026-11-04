export interface Recording {
	id: string;
	user_created?: string;
	date_created?: string;
	title: string;
	audio_file?: string;
	duration_seconds?: number;
	language: string;
	profile: string;
	status: "pending" | "transcribing" | "done" | "error";
	error_message?: string | null;
	source: "batch" | "live";
	// Review workflow status, distinct from the transcription `status` above:
	// whether the user is still editing the text or has marked it ready.
	review_status?: "editing" | "ready";
	hub_job_id?: number | null;
	hub_job_key?: string | null;
}
export interface Transcript {
	id: string;
	recording: string;
	text: string;
	structured?: Record<string, unknown> | null;
	segments?: unknown[] | null;
	engine?: string;
	date_created?: string;
	date_updated?: string;
}
export interface ResonaDocument {
	id: string;
	user_created?: string;
	date_created?: string;
	date_updated?: string;
	title: string;
	file?: string;
	status: "pending" | "processing" | "done" | "error";
	error_message?: string | null;
	markdown?: string | null;
	pages?: unknown[] | null;
	hub_job_id?: number | null;
	hub_job_key?: string | null;
}
export interface Schema {
	recordings: Recording[];
	transcripts: Transcript[];
	documents: ResonaDocument[];
}
