/** Hub JobResult.provenance as stored with a transcript/document. */
export interface StageProvenance {
	stage: string;
	engine: string;
	model: string;
	accelerator?: string;
	seconds: number;
}
export interface Provenance {
	worker_id?: string | null;
	runtime_version?: string | null;
	stages?: StageProvenance[];
}
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
	provenance?: Provenance | null;
	/**
	 * The transcript a postprocess profile runs on ("Nachbearbeiten"). Null while
	 * `text` itself is the raw transcript; set once a profile's output replaced `text`.
	 */
	raw_text?: string | null;
	/** Profile whose output `text` is; null for the raw transcript. */
	applied_profile?: string | null;
	/** Re-processing as a hub text job: pending -> processing -> done | error. */
	postprocess_status?: "pending" | "processing" | "done" | "error" | null;
	postprocess_profile?: string | null;
	postprocess_error?: string | null;
	/** Hub `queue_reason` while the text job waits (e.g. `no_capable_worker`). */
	postprocess_queue_reason?: string | null;
	postprocess_job_id?: number | null;
	postprocess_job_key?: string | null;
	date_created?: string;
	date_updated?: string;
}
/** A user's edit of an ASR transcript, kept as training/evaluation data for the engines. */
export interface TranscriptCorrection {
	id: string;
	transcript: string;
	recording: string;
	/** Text before and after the save (whole transcript). */
	before: string;
	after: string;
	/** Changed spans (word level): what the engine wrote vs what the user made of it. */
	edits: CorrectionEdit[];
	engine?: string | null;
	language?: string | null;
	user_created?: string;
	date_created?: string;
}
export interface CorrectionEdit {
	before: string;
	after: string;
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
	provenance?: Provenance | null;
	hub_job_id?: number | null;
	hub_job_key?: string | null;
}
export interface Schema {
	recordings: Recording[];
	transcripts: Transcript[];
	documents: ResonaDocument[];
	transcript_corrections: TranscriptCorrection[];
}
