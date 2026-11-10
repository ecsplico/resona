// TEMPORARY DIAGNOSTIC ROUTE — remove once live dictation and on-device title
// generation work on the phones in question.
//
// This is a deliberate, time-boxed exception to the PWA's "no server logic
// beyond the OAuth layer" rule: `/debug` → "An Server senden" POSTs the
// diagnostics report of a phone here, and the route writes it as ONE stdout
// line `[resona-debug] <json>` so it can be read in the Coolify logs (phones
// have no usable devtools). It stores nothing and returns only a short id the
// user can quote. Unauthenticated, so each client IP may send
// `DEBUG_REPORT_RATE_LIMIT` reports per window (429 beyond), the body is capped
// at 32 KB while it is read (413; a missing or lying Content-Length never makes
// the route buffer more than the cap plus one chunk) and re-redacted
// server-side (no tokens, no transcript text reach the log).
//
// Explicit h3 imports (instead of Nitro auto-imports) keep the handler
// runnable in the spec via `toWebHandler`.
import type { H3Event } from "h3";
import { createError, defineEventHandler, getRequestHeader, getRequestIP } from "h3";
import { DEBUG_REPORT_MAX_BYTES, createRateLimiter, parseDebugReport, redactDebugReport } from "#shared/debugReport";

const limiter = createRateLimiter();

function tooLarge(): Error {
	return createError({ statusCode: 413, statusMessage: "Payload Too Large", message: `Bericht größer als ${DEBUG_REPORT_MAX_BYTES} Bytes` });
}

// Reads the body chunk by chunk and stops at the first chunk that crosses
// `max`: a web stream is cancelled, a Node request is detached and drained
// without keeping its bytes.
async function readCappedBody(event: H3Event, max: number): Promise<string> {
	const chunks: Uint8Array[] = [];
	let total = 0;
	const take = (chunk: Uint8Array): boolean => {
		total += chunk.byteLength;
		if (total > max) {
			return false;
		}
		chunks.push(chunk);
		return true;
	};
	const webBody = event.web?.request?.body;
	if (webBody) {
		const reader = webBody.getReader();
		for (;;) {
			const { done, value } = await reader.read();
			if (done) {
				break;
			}
			if (!take(value)) {
				await reader.cancel();
				throw tooLarge();
			}
		}
	}
	else {
		const req = event.node.req;
		const { promise, resolve, reject } = Promise.withResolvers<undefined>();
		const onData = (chunk: Uint8Array) => {
			if (!take(chunk)) {
				cleanup();
				req.resume();
				reject(tooLarge());
			}
		};
		const onEnd = () => {
			cleanup();
			resolve(undefined);
		};
		const onError = (err: Error) => {
			cleanup();
			reject(err);
		};
		const cleanup = () => {
			req.off("data", onData);
			req.off("end", onEnd);
			req.off("error", onError);
		};
		req.on("data", onData);
		req.on("end", onEnd);
		req.on("error", onError);
		await promise;
	}
	const body = new Uint8Array(total);
	let offset = 0;
	for (const chunk of chunks) {
		body.set(chunk, offset);
		offset += chunk.byteLength;
	}
	return new TextDecoder().decode(body);
}

export default defineEventHandler(async (event) => {
	const ip = getRequestIP(event, { xForwardedFor: true }) ?? "unknown";
	if (!limiter.allow(ip)) {
		throw createError({ statusCode: 429, statusMessage: "Too Many Requests", message: "Zu viele Berichte, bitte später erneut senden" });
	}
	const declared = Number(getRequestHeader(event, "content-length"));
	if (Number.isFinite(declared) && declared > DEBUG_REPORT_MAX_BYTES) {
		throw tooLarge();
	}
	const parsed = parseDebugReport(await readCappedBody(event, DEBUG_REPORT_MAX_BYTES));
	if (!parsed.ok) {
		throw createError({ statusCode: parsed.status, statusMessage: parsed.status === 413 ? "Payload Too Large" : "Bad Request", message: parsed.message });
	}
	const id = globalThis.crypto.randomUUID().slice(0, 8);
	const line = { id, receivedAt: new Date().toISOString(), report: redactDebugReport(parsed.report) };
	console.log(`[resona-debug] ${JSON.stringify(line)}`);
	return { id };
});
