// Shared error-classification helpers used by the UI.

/**
 * True when a stored transcription `error_message` indicates the backend was
 * unreachable / timed out (a transient connectivity problem) rather than a
 * genuine processing failure. Drives the distinct "offline" banner styling.
 * Matches the German messages the transcribe worker produces.
 */
export function isServiceUnreachable(message: string | null | undefined): boolean {
	if (!message) {
		return false;
	}
	return /nicht erreichbar|zeitüberschreitung/i.test(message);
}
