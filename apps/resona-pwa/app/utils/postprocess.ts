import type { Transcript } from "~/types/directus";
import type { TranscriptPatch } from "~/data/types";

/**
 * Re-processing a stored transcript with a hub postprocess profile ("Nachbearbeiten"):
 * shared by the standalone processor, the detail page and (same messages) the
 * project worker (`worker/src/resona_project_worker/worker.py`).
 */

/** German messages for the hub error codes of a text job. */
const ERRORS: Record<string, string> = {
	profile_required: "Kein Nachbearbeitungsprofil gewählt.",
	profile_not_found: "Profil auf dem Hub nicht gefunden.",
	invalid_profile: "Das Profil auf dem Hub ist ungültig.",
	policy_denied: "Von der Richtlinie der Organisation abgelehnt.",
};

/** German message for a text-job error code; null for codes without one. `policy_denied` carries the hub's reason. */
export function postprocessErrorMessage(code: string | null | undefined, detail?: string | null): string | null {
	const message = ERRORS[code ?? ""];
	if (message === undefined) return null;
	if (code === "policy_denied" && detail) return `${message.slice(0, -1)}: ${detail}`;
	return message;
}

/** Why a waiting text job does not run yet, for the hub's `queue_reason`. */
export function queueReasonMessage(reason: string | null | undefined): string | null {
	if (reason === "no_capable_worker") {
		return "Kein passender Worker online – die Nachbearbeitung startet, sobald er wieder erreichbar ist.";
	}
	if (reason === "waiting_capacity") return "Wartet auf einen freien Worker.";
	return null;
}

/** The raw transcript a profile runs on: `raw_text`, else (never re-processed) `text`. */
export function postprocessInput(t: Pick<Transcript, "text" | "raw_text">): string {
	return t.raw_text ?? t.text ?? "";
}

/** True while a re-processing request is queued or runs on the hub. */
export function postprocessRunning(t: Pick<Transcript, "postprocess_status"> | null | undefined): boolean {
	return t?.postprocess_status === "pending" || t?.postprocess_status === "processing";
}

/**
 * Fields that request a run of `profile`. The processor's claim clears the previous
 * run's job, error and queue reason (users may not write those fields).
 */
export function postprocessRequest(profile: string): TranscriptPatch {
	return { postprocess_status: "pending", postprocess_profile: profile };
}

/** Fields that make the raw transcript the text again (undo a profile). */
export function rawRestore(t: Pick<Transcript, "text" | "raw_text">): TranscriptPatch {
	return { text: postprocessInput(t), raw_text: null, applied_profile: null, processed_text: null, postprocess_status: null };
}

/**
 * True when re-running or restoring would drop changes: a profile was applied and
 * `text` differs from that profile's output as delivered.
 */
export function editedSinceProfile(t: Pick<Transcript, "applied_profile" | "processed_text">, text: string): boolean {
	return t.applied_profile != null && text !== (t.processed_text ?? null);
}

/**
 * `raw_text` with new raw content (dictation, a continued recording part) appended
 * as its own paragraph, so a re-run or restore keeps it. Null when no profile is
 * applied (then `text` is the raw transcript and nothing else needs it).
 */
export function appendToRaw(t: Pick<Transcript, "text" | "raw_text" | "applied_profile">, parts: string[]): string | null {
	if (t.applied_profile == null) return null;
	return parts.reduce((raw, part) => {
		const piece = part.trim();
		if (!piece) return raw;
		return raw.length === 0 || raw.endsWith("\n") ? raw + piece : `${raw}\n${piece}`;
	}, postprocessInput(t));
}

/** Fields written once a text job delivered `markdown` for `profile`. */
export function postprocessDone(t: Pick<Transcript, "text" | "raw_text">, markdown: string, profile: string): Partial<Transcript> {
	return {
		text: markdown,
		raw_text: postprocessInput(t),
		applied_profile: profile,
		processed_text: markdown,
		postprocess_status: "done",
		postprocess_error: null,
		postprocess_queue_reason: null,
		postprocess_job_id: null,
		postprocess_job_key: null,
	};
}

/** Fields of a failed run; text and raw transcript stay untouched. */
export function postprocessFailed(message: string): Partial<Transcript> {
	return {
		postprocess_status: "error",
		postprocess_error: message.slice(0, 1000),
		postprocess_queue_reason: null,
		postprocess_job_id: null,
		postprocess_job_key: null,
	};
}
