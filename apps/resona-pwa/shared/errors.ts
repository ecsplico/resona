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

// Raw browser fetch-failure strings — never useful to end-users.
const NETWORK_ERROR_RE = /NetworkError when attempting to fetch|Failed to fetch|Load failed|network error/i;

/**
 * Convert a caught error into a human-readable string.
 * Maps raw browser fetch errors to a single "Server nicht erreichbar." message.
 * Falls back to `fallback` for non-Error values.
 */
export function humanizeError(err: unknown, fallback: string): string {
	if (!(err instanceof Error)) return fallback;
	if (NETWORK_ERROR_RE.test(err.message)) return "Server nicht erreichbar.";
	return err.message || fallback;
}
