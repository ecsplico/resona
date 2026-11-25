import { HubError, type JobContext } from "~/composables/useHub";
import { resolveInsertion, snapshotInsertion, type InsertionResult, type InsertionTarget } from "~/utils/insertionGuard";

const DEFAULT_TIMEOUT_MS = 60_000;
const POLL_FIRST_MS = 500;
const POLL_MAX_MS = 3000;
/** The hub caps each context part at 4000 characters (job-core `MAX_CONTEXT_CHARS`). */
export const CONTEXT_MAX_CHARS = 4000;

export interface LiveCleanupOptions {
	/** Postprocess profile on the hub (the org's `live.cleanup_profile`). */
	profile: string;
	language?: string;
	/** Text around the spot the cleaned text goes; sent as the job's `context` when given. */
	context?: JobContext | null;
	signal?: AbortSignal;
	timeoutMs?: number;
}

/**
 * The job `context` of a finished live text: the heading it is filed under (`before`),
 * cut to the hub's limit. Null when there is no title, so the job is sent without one.
 */
export function liveCleanupContext(title: string): JobContext | null {
	const heading = title.trim();
	if (!heading) return null;
	return { before: `# ${heading}`.slice(0, CONTEXT_MAX_CHARS) };
}

/** Ends a cleanup early; `reason` is the code logged (never the text). */
class Stop extends Error {
	constructor(readonly reason: string) {
		super(reason);
	}
}

// Executor form instead of Promise.withResolvers: iOS Safari < 17.4 (as in useTitleJob).
function sleep(ms: number): Promise<void> {
	return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Cleanup of finished live text on the hub: a `kind: "text"` job with the org's
 * `live.cleanup_profile` and a `context`. The result goes through the insertion guard:
 * it replaces the text it was made from only while that is unchanged and still owned
 * by the same session, else it is dropped. Best effort: every failure leaves the text.
 */
export function useLiveCleanup() {
	const hub = useHub();

	/** The cleaned text of `text`, or null (failure, abort, timeout, empty answer). */
	async function cleanText(text: string, opts: LiveCleanupOptions): Promise<string | null> {
		const deadline = Date.now() + (opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
		const check = (): void => {
			if (opts.signal?.aborted) throw new Stop("aborted");
			if (Date.now() >= deadline) throw new Stop("timeout");
		};
		let jobId: number | null = null;
		let reason = "ok";
		try {
			if (!hub.hasToken()) throw new Stop("no_token");
			const input = text.trim();
			if (!input) throw new Stop("empty");
			const file = new File([input], "live.md", { type: "text/markdown" });
			const uploadId = await hub.upload(file, file.name);
			check();
			jobId = await hub.createJob(
				{
					kind: "text",
					upload_id: uploadId,
					profile: opts.profile,
					...(opts.context ? { context: opts.context } : {}),
					...(opts.language ? { language: opts.language } : {}),
				},
				`live-cleanup:${crypto.randomUUID()}`,
			);
			let wait = POLL_FIRST_MS;
			for (;;) {
				check();
				const job = await hub.getJob(jobId);
				if (job.status === "completed") break;
				if (job.status === "failed" || job.status === "cancelled") {
					throw new Stop(`job_${job.status}:${job.error?.code ?? "unknown"}`);
				}
				await sleep(Math.min(wait, Math.max(0, deadline - Date.now())));
				wait = Math.min(wait * 2, POLL_MAX_MS);
			}
			check();
			const cleaned = (await hub.getResult(jobId)).markdown?.trim() ?? "";
			if (!cleaned) reason = "empty_result";
			return cleaned || null;
		}
		catch (err) {
			reason = err instanceof Stop ? err.reason : err instanceof HubError ? `hub:${err.code}` : "error";
			return null;
		}
		finally {
			if (jobId !== null) {
				try {
					await hub.deleteJob(jobId);
				}
				catch {
					// The hub expires jobs on its own.
				}
			}
			console.info(`[live-cleanup] ${reason}`);
		}
	}

	/**
	 * Clean `target.text` and land the result through the insertion guard. `current`
	 * is read when the result arrives; the return value says whether it was inserted
	 * (with the new text) or dropped. Null when the cleanup produced nothing.
	 */
	async function cleanupInto(
		target: InsertionTarget,
		current: () => InsertionTarget | null,
		opts: LiveCleanupOptions & { language: string },
	): Promise<InsertionResult | null> {
		const snapshot = snapshotInsertion(target);
		const cleaned = await cleanText(target.text, opts);
		if (cleaned === null) return null;
		return resolveInsertion(snapshot, current(), cleaned, { language: opts.language, onChange: "drop" });
	}

	return { cleanText, cleanupInto };
}
