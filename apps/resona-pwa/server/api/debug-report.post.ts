// TEMPORARY DIAGNOSTIC ROUTE — remove once live dictation and on-device title
// generation work on the phones in question.
//
// This is a deliberate, time-boxed exception to the PWA's "no server logic
// beyond the OAuth layer" rule: `/debug` → "An Server senden" POSTs the
// diagnostics report of a phone here, and the route writes it as ONE stdout
// line `[resona-debug] <json>` so it can be read in the Coolify logs (phones
// have no usable devtools). It stores nothing and returns only a short id the
// user can quote. Unauthenticated, so the body is capped at 32 KB and
// re-redacted server-side (no tokens, no transcript text reach the log).
//
// Explicit h3 imports (instead of Nitro auto-imports) keep the handler
// runnable in the spec via `toWebHandler`.
import { createError, defineEventHandler, getRequestHeader, readRawBody } from "h3";
import { DEBUG_REPORT_MAX_BYTES, parseDebugReport, redactDebugReport } from "#shared/debugReport";

export default defineEventHandler(async (event) => {
	const declared = Number(getRequestHeader(event, "content-length"));
	if (Number.isFinite(declared) && declared > DEBUG_REPORT_MAX_BYTES) {
		throw createError({ statusCode: 413, statusMessage: "Payload Too Large", message: `Bericht größer als ${DEBUG_REPORT_MAX_BYTES} Bytes` });
	}
	const parsed = parseDebugReport(await readRawBody(event, "utf8"));
	if (!parsed.ok) {
		throw createError({ statusCode: parsed.status, statusMessage: parsed.status === 413 ? "Payload Too Large" : "Bad Request", message: parsed.message });
	}
	const id = globalThis.crypto.randomUUID().slice(0, 8);
	const line = { id, receivedAt: new Date().toISOString(), report: redactDebugReport(parsed.report) };
	console.log(`[resona-debug] ${JSON.stringify(line)}`);
	return { id };
});
