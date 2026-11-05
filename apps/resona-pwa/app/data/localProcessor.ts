import type { Recording, ResonaDocument } from "~/types/directus";
import { HubError, type HubJob } from "~/composables/useHub";
import {
	listByStatus,
	patchDocumentInternal,
	patchRecordingInternal,
	readFile,
	upsertTranscript,
} from "~/data/localStore";

/**
 * Standalone-mode processor: drives locally stored recordings/documents through
 * the hub's job API (submit → poll → result). Same state machine as the
 * resona-project-worker. Runs only while the app is open.
 */

type Collection = "recordings" | "documents";
type Row = Recording | ResonaDocument;

const MAX_SUBMIT_PER_CYCLE = 5;

interface Spec {
	collection: Collection;
	kind: "audio" | "document";
	active: Recording["status"] | ResonaDocument["status"];
	fileOf: (row: Row) => string | undefined;
	fallbackName: (id: string) => string;
	patch: (id: string, patch: Record<string, unknown>) => Promise<unknown>;
}

const SPECS: Spec[] = [
	{
		collection: "recordings",
		kind: "audio",
		active: "transcribing",
		fileOf: row => (row as Recording).audio_file,
		fallbackName: id => `${id}.webm`,
		patch: (id, p) => patchRecordingInternal(id, p as Partial<Recording>),
	},
	{
		collection: "documents",
		kind: "document",
		active: "processing",
		fileOf: row => (row as ResonaDocument).file,
		fallbackName: id => `${id}.bin`,
		patch: (id, p) => patchDocumentInternal(id, p as Partial<ResonaDocument>),
	},
];

// Same transient set as the Python worker: offline, rate-limited, gateway errors.
const TRANSIENT_STATUSES = new Set([0, 429, 502, 503, 504]);

/** Hub errors worth retrying later: offline, rate-limited, gateway errors. */
export function isTransient(err: unknown): boolean {
	return err instanceof HubError && TRANSIENT_STATUSES.has(err.status);
}

/** German message for a job that ended without a result; null while it runs or once it completed. */
export function jobFailureMessage(job: HubJob): string | null {
	if (job.status !== "failed" && job.status !== "cancelled") return null;
	return `Verarbeitung fehlgeschlagen: ${job.error?.message ?? job.error?.code ?? job.status}`;
}

/**
 * German message for a permanent hub error while reading a job (`getJob`) or its
 * result (`getResult`); null for anything that is not a `HubError`. Check
 * `isTransient` first: transient errors are retried, not mapped.
 */
export function jobReadErrorMessage(err: unknown, step: "job" | "result"): string | null {
	if (!(err instanceof HubError)) return null;
	if (step === "result" && err.status === 410) return "Ergebnis auf dem Hub abgelaufen";
	if (err.status === 404) return "Hub-Job nicht gefunden";
	return err.message;
}

/** Deletes a finished hub job; the hub expires jobs on its own, so a failed cleanup is not an error. */
export async function deleteJobQuietly(hub: { deleteJob(id: number): Promise<void> }, jobId: number): Promise<void> {
	try {
		await hub.deleteJob(jobId);
	}
	catch {
		// ignored, see above
	}
}

async function fail(spec: Spec, id: string, message: string): Promise<void> {
	await spec.patch(id, { status: "error", error_message: message, hub_job_id: null, hub_job_key: null });
}

async function submit(spec: Spec, row: Row, hub: ReturnType<typeof useHub>): Promise<void> {
	const fileId = spec.fileOf(row);
	const blob = fileId ? await readFile(fileId) : null;
	if (!blob) {
		await fail(spec, row.id, "Datei fehlt");
		return;
	}
	const name = blob instanceof File && blob.name ? blob.name : spec.fallbackName(row.id);
	// Persist the idempotency key before the create call: if the hub accepts the job but
	// the response is lost, the retry reuses the key and the hub returns the original job.
	const key = row.hub_job_key ?? `local:${spec.collection}:${row.id}:${crypto.randomUUID()}`;
	try {
		if (row.hub_job_key == null) await spec.patch(row.id, { hub_job_key: key });
		const uploadId = await hub.upload(blob, name);
		const body = spec.kind === "audio"
			? { kind: spec.kind, upload_id: uploadId, language: (row as Recording).language, profile: (row as Recording).profile }
			: { kind: spec.kind, upload_id: uploadId };
		const jobId = await hub.createJob(body, key);
		await spec.patch(row.id, { status: spec.active, hub_job_id: jobId, error_message: null });
	}
	catch (err) {
		if (isTransient(err)) return;
		await fail(spec, row.id, err instanceof Error ? err.message : String(err));
	}
}

async function poll(spec: Spec, row: Row, hub: ReturnType<typeof useHub>): Promise<void> {
	const jobId = row.hub_job_id as number;
	let job;
	try {
		job = await hub.getJob(jobId);
	}
	catch (err) {
		if (isTransient(err)) return;
		const message = jobReadErrorMessage(err, "job");
		if (message === null) throw err;
		await fail(spec, row.id, message);
		return;
	}
	const failure = jobFailureMessage(job);
	if (failure !== null) {
		await fail(spec, row.id, failure);
		return;
	}
	if (job.status !== "completed") return;
	let result;
	try {
		result = await hub.getResult(jobId);
	}
	catch (err) {
		if (isTransient(err)) return;
		const message = jobReadErrorMessage(err, "result");
		if (message === null) throw err;
		await fail(spec, row.id, message);
		return;
	}
	if (spec.kind === "audio") {
		await upsertTranscript(row.id, {
			text: result.markdown,
			segments: result.segments,
			structured: result.structured,
			engine: job.candidate?.engine,
		});
		await spec.patch(row.id, { status: "done", hub_job_id: null, hub_job_key: null });
	}
	else {
		await spec.patch(row.id, { markdown: result.markdown, pages: result.pages, status: "done", hub_job_id: null, hub_job_key: null });
	}
	await deleteJobQuietly(hub, jobId);
}

async function isolated(work: () => Promise<void>): Promise<void> {
	try {
		await work();
	}
	catch (err) {
		console.warn("[localProcessor] row failed", err);
	}
}

export async function runLocalCycle(): Promise<void> {
	if (typeof navigator !== "undefined" && navigator.onLine === false) return;
	const hub = useHub();
	if (!hub.hasToken()) return;

	for (const spec of SPECS) {
		const active = (await listByStatus(spec.collection, spec.active)) as Row[];
		for (const row of active) {
			if (row.hub_job_id == null) {
				await isolated(() => spec.patch(row.id, { status: "pending" }).then(() => undefined));
			}
		}
		const pending = ((await listByStatus(spec.collection, "pending")) as Row[]).slice(0, MAX_SUBMIT_PER_CYCLE);
		for (const row of pending) {
			await isolated(() => submit(spec, row, hub));
		}
		for (const row of active) {
			if (row.hub_job_id != null) {
				await isolated(() => poll(spec, row, hub));
			}
		}
	}
}
