// Wire contract of the TEMPORARY mobile diagnostics report (`/debug` →
// `POST /api/debug-report`). Shared by the browser (redacts before sending)
// and the Nitro route (validates the size and redacts again before logging),
// so neither side alone has to be trusted to keep secrets out of the log.

/** Largest accepted report body, in UTF-8 bytes. */
export const DEBUG_REPORT_MAX_BYTES = 32 * 1024;

export const REDACTED = "[redacted]";

// Keys whose value is never logged: credentials and dictated content.
const SECRET_KEY_RE = /^(?:token|access_?token|refresh_?token|api_?key|authorization|password|secret|transcript|text)$/i;
// Credentials that ride in URLs (the live WebSocket sends `?token=`).
const SECRET_PARAM_RE = /([?&](?:token|api_key|access_token)=)[^&#\s"]*/gi;
const BEARER_RE = /\bBearer\s+\S+/gi;
const MAX_DEPTH = 12;

function redactString(value: string, secrets: string[]): string {
	let out = value;
	for (const secret of secrets) {
		out = out.split(secret).join(REDACTED);
	}
	return out.replace(SECRET_PARAM_RE, `$1${REDACTED}`).replace(BEARER_RE, `Bearer ${REDACTED}`);
}

function walk(value: unknown, secrets: string[], depth: number): unknown {
	if (typeof value === "string") {
		return redactString(value, secrets);
	}
	if (value === null || typeof value !== "object") {
		return value;
	}
	if (depth >= MAX_DEPTH) {
		return REDACTED;
	}
	if (Array.isArray(value)) {
		return value.map(item => walk(item, secrets, depth + 1));
	}
	const out: Record<string, unknown> = {};
	for (const [key, item] of Object.entries(value)) {
		out[key] = SECRET_KEY_RE.test(key) ? REDACTED : walk(item, secrets, depth + 1);
	}
	return out;
}

/**
 * Deep copy of `value` without credentials or dictated text: every value under
 * a secret-shaped key (`token`, `authorization`, `transcript`, `text`, …), every
 * `?token=`/`Bearer` credential and every literal occurrence of `secrets`
 * (e.g. the device's hub token, ignored when shorter than 4 characters) is
 * replaced by `[redacted]`.
 */
export function redactDebugReport<T>(value: T, secrets: readonly string[] = []): T {
	const usable = secrets.filter(s => typeof s === "string" && s.length >= 4);
	return walk(value, usable, 0) as T;
}

export type DebugReportParse
	= | { ok: true; report: Record<string, unknown> }
		| { ok: false; status: 400 | 413; message: string };

export function byteLength(text: string): number {
	return new TextEncoder().encode(text).length;
}

/** Validate a raw report body: ≤ `DEBUG_REPORT_MAX_BYTES`, a JSON object. */
export function parseDebugReport(raw: string | null | undefined): DebugReportParse {
	if (!raw) {
		return { ok: false, status: 400, message: "Leerer Bericht" };
	}
	if (byteLength(raw) > DEBUG_REPORT_MAX_BYTES) {
		return { ok: false, status: 413, message: `Bericht größer als ${DEBUG_REPORT_MAX_BYTES} Bytes` };
	}
	let parsed: unknown;
	try {
		parsed = JSON.parse(raw);
	}
	catch {
		return { ok: false, status: 400, message: "Bericht ist kein JSON" };
	}
	if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
		return { ok: false, status: 400, message: "Bericht muss ein JSON-Objekt sein" };
	}
	return { ok: true, report: parsed as Record<string, unknown> };
}
