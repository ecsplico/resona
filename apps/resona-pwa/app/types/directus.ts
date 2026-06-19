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
	engine?: string;
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
export interface Schema {
	recordings: Recording[];
	transcripts: Transcript[];
}
