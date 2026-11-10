import { HubError, type HubResult } from "~/composables/useHub";

/** Hub profile (bundled in resona-postprocess, pushed with `resona profiles push`): one extract step named `title`. */
export const TITLE_PROFILE = "title-de";
/** Only the beginning of a text goes to the model. */
export const TITLE_INPUT_MAX_CHARS = 4000;
export const REMOTE_TITLE_MAX_CHARS = 80;
const DEFAULT_TIMEOUT_MS = 20_000;
const POLL_FIRST_MS = 500;
const POLL_MAX_MS = 3000;

export interface SuggestTitleOptions {
	language?: string;
	signal?: AbortSignal;
	timeoutMs?: number;
}

/** First ≤ `TITLE_INPUT_MAX_CHARS` characters of `text`, cut at a word boundary when one is near the end. */
export function truncateForTitle(text: string): string {
	const trimmed = text.trim();
	if (trimmed.length <= TITLE_INPUT_MAX_CHARS) return trimmed;
	const head = trimmed.slice(0, TITLE_INPUT_MAX_CHARS);
	const cut = head.search(/\s\S*$/);
	return (cut > TITLE_INPUT_MAX_CHARS / 2 ? head.slice(0, cut) : head).trimEnd();
}

/**
 * The title from a result of the `title-de` profile. The extract step `title` stores its JSON
 * object under its name, so the title sits at `structured.title.title`; a bare string at
 * `structured.title` is accepted too. Null when missing, empty or longer than `REMOTE_TITLE_MAX_CHARS`.
 */
export function titleFromResult(result: Pick<HubResult, "structured">): string | null {
	const structured = result.structured as { title?: unknown } | null | undefined;
	const entry = structured?.title;
	const raw = typeof entry === "string" ? entry : (entry as { title?: unknown } | null | undefined)?.title;
	if (typeof raw !== "string") return null;
	const title = raw.replace(/\s+/g, " ").replace(/^["'„“”«»\s]+/, "").replace(/["'„“”«».,;:\s]+$/, "");
	return title.length > 0 && title.length <= REMOTE_TITLE_MAX_CHARS ? title : null;
}

/** Ends a suggestion early; `reason` is the code logged (never the text). */
class Stop extends Error {
	constructor(readonly reason: string) {
		super(reason);
	}
}

/**
 * Title suggestions from a small model on the hub: a `kind: "text"` job with the
 * `title-de` profile. Best effort: every failure resolves to null.
 */
export function useTitleJob() {
	const hub = useHub();

	async function suggestTitleRemote(text: string, opts: SuggestTitleOptions = {}): Promise<string | null> {
		const { signal } = opts;
		const deadline = Date.now() + (opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
		let jobId: number | null = null;

		function check(): void {
			if (signal?.aborted) throw new Stop("aborted");
			if (Date.now() >= deadline) throw new Stop("timeout");
		}

		/** `work`, or a `Stop` as soon as the caller aborts or the deadline passes. */
		// (Executor form instead of Promise.withResolvers: iOS Safari < 17.4.)
		function guard<T>(work: Promise<T>): Promise<T> {
			check();
			return new Promise<T>((resolve, reject) => {
				const stop = (reason: string) => {
					cleanup();
					reject(new Stop(reason));
				};
				const onAbort = () => stop("aborted");
				const timer = setTimeout(() => stop("timeout"), Math.max(0, deadline - Date.now()));
				const cleanup = () => {
					clearTimeout(timer);
					signal?.removeEventListener("abort", onAbort);
				};
				signal?.addEventListener("abort", onAbort, { once: true });
				work.then(
					(value) => {
						cleanup();
						resolve(value);
					},
					(err: unknown) => {
						cleanup();
						reject(err);
					},
				);
			});
		}

		let finished = false;
		let reason = "ok";
		try {
			if (!hub.hasToken()) throw new Stop("no_token");
			const input = truncateForTitle(text);
			if (!input) throw new Stop("empty");

			const file = new File([input], "title-input.md", { type: "text/markdown" });
			const uploadId = await guard(hub.upload(file, file.name));
			const creating = hub.createJob(
				{ kind: "text", upload_id: uploadId, profile: TITLE_PROFILE, ...(opts.language ? { language: opts.language } : {}) },
				`title:${crypto.randomUUID()}`,
			);
			// A job created after we gave up is still deleted.
			creating.then(id => (finished ? quietDelete(id) : undefined), () => undefined);
			jobId = await guard(creating);

			let wait = POLL_FIRST_MS;
			for (;;) {
				const job = await guard(hub.getJob(jobId));
				if (job.status === "completed") break;
				if (job.status === "failed" || job.status === "cancelled") {
					throw new Stop(`job_${job.status}:${job.error?.code ?? "unknown"}`);
				}
				await guard(new Promise<void>(resolve => setTimeout(resolve, Math.min(wait, Math.max(0, deadline - Date.now())))));
				wait = Math.min(wait * 2, POLL_MAX_MS);
			}
			const title = titleFromResult(await guard(hub.getResult(jobId)));
			if (title === null) reason = "no_title";
			return title;
		}
		catch (err) {
			reason = err instanceof Stop ? err.reason : err instanceof HubError ? `hub:${err.code}` : "error";
			return null;
		}
		finally {
			finished = true;
			if (jobId !== null) await quietDelete(jobId);
			console.info(`[title] remote suggestion: ${reason}`);
		}
	}

	async function quietDelete(id: number): Promise<void> {
		try {
			await hub.deleteJob(id);
		}
		catch {
			// The hub expires jobs on its own.
		}
	}

	return { suggestTitleRemote };
}
