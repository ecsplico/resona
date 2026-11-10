import type { Recording, ResonaDocument, Transcript } from "~/types/directus";
import { HubError, type HubJob } from "~/composables/useHub";
import {
	listByStatus,
	listTranscriptsByPostprocess,
	patchDocumentInternal,
	patchRecordingInternal,
	patchTranscriptInternal,
	readFile,
	readRecordingInternal,
	upsertTranscript,
} from "~/data/localStore";
import { suggestTitle } from "~/utils/titleSuggestion";
import {
	postprocessDone,
	postprocessErrorMessage,
	postprocessFailed,
	postprocessInput,
} from "~/utils/postprocess";

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
		const { supersededJob } = await upsertTranscript(row.id, {
			text: result.markdown,
			segments: result.segments,
			structured: result.structured,
			engine: job.candidate?.engine,
			provenance: result.provenance,
		});
		// Re-read: a rename during the awaits above clears title_auto and must win.
		const current = await readRecordingInternal(row.id);
		const title = current?.title_auto ? suggestTitle(result.markdown) : null;
		await spec.patch(row.id, {
			status: "done",
			hub_job_id: null,
			hub_job_key: null,
			...(title !== null ? { title, title_auto: false } : {}),
		});
		// A text job still running on the previous transcript would write stale output.
		if (supersededJob !== null) await deleteJobQuietly(hub, supersededJob);
	}
	else {
		await spec.patch(row.id, { markdown: result.markdown, pages: result.pages, provenance: result.provenance, status: "done", hub_job_id: null, hub_job_key: null });
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

// ── Re-processing a stored transcript as a hub text job ("Nachbearbeiten") ──

async function submitText(t: Transcript, hub: ReturnType<typeof useHub>): Promise<void> {
	const profile = t.postprocess_profile;
	const source = postprocessInput(t);
	if (!profile) {
		await patchTranscriptInternal(t.id, postprocessFailed(postprocessErrorMessage("profile_required") as string));
		return;
	}
	if (!source.trim()) {
		await patchTranscriptInternal(t.id, postprocessFailed("Kein Transkript-Text zum Nachbearbeiten."));
		return;
	}
	// A pending row that still carries a job was re-requested while that job ran
	// (another tab or device): the old run is superseded, so delete it on the hub and
	// submit under a fresh key (the old key would return the old job).
	const superseded = t.postprocess_job_id ?? null;
	// Key persisted before the create call, as for recordings (lost response -> same job).
	const key = (superseded === null ? t.postprocess_job_key : null) ?? `local:transcripts:${t.id}:${crypto.randomUUID()}`;
	try {
		// Claim: clears the previous run's job, error and queue reason.
		await patchTranscriptInternal(t.id, { postprocess_job_key: key, postprocess_job_id: null, postprocess_error: null, postprocess_queue_reason: null });
		if (superseded !== null) await deleteJobQuietly(hub, superseded);
		const file = new File([source], "transcript.md", { type: "text/markdown" });
		const uploadId = await hub.upload(file, file.name);
		const jobId = await hub.createJob({ kind: "text", upload_id: uploadId, profile }, key);
		await patchTranscriptInternal(t.id, { postprocess_status: "processing", postprocess_job_id: jobId, postprocess_error: null });
	}
	catch (err) {
		if (isTransient(err)) return;
		const mapped = err instanceof HubError ? postprocessErrorMessage(err.code, err.message) : null;
		await patchTranscriptInternal(t.id, postprocessFailed(mapped ?? (err instanceof Error ? err.message : String(err))));
	}
}

async function pollText(t: Transcript, hub: ReturnType<typeof useHub>): Promise<void> {
	const jobId = t.postprocess_job_id as number;
	let job;
	try {
		job = await hub.getJob(jobId);
	}
	catch (err) {
		if (isTransient(err)) return;
		const message = jobReadErrorMessage(err, "job");
		if (message === null) throw err;
		await patchTranscriptInternal(t.id, postprocessFailed(message));
		return;
	}
	const failure = jobFailureMessage(job);
	if (failure !== null) {
		const mapped = job.error?.code === "policy_denied" ? postprocessErrorMessage("policy_denied", job.error.message) : null;
		await patchTranscriptInternal(t.id, postprocessFailed(mapped ?? failure));
		return;
	}
	if (job.status !== "completed") {
		// Tell the user why it waits (e.g. `no_capable_worker`); written only when it changes.
		const reason = job.queue_reason ?? null;
		if (reason !== (t.postprocess_queue_reason ?? null)) {
			await patchTranscriptInternal(t.id, { postprocess_queue_reason: reason });
		}
		return;
	}
	let result;
	try {
		result = await hub.getResult(jobId);
	}
	catch (err) {
		if (isTransient(err)) return;
		const message = jobReadErrorMessage(err, "result");
		if (message === null) throw err;
		await patchTranscriptInternal(t.id, postprocessFailed(message));
		return;
	}
	await patchTranscriptInternal(t.id, postprocessDone(t, result.markdown ?? "", t.postprocess_profile ?? ""));
	await deleteJobQuietly(hub, jobId);
}

async function runTextJobs(hub: ReturnType<typeof useHub>): Promise<void> {
	const active = await listTranscriptsByPostprocess("processing");
	for (const t of active) {
		if (t.postprocess_job_id == null) {
			await isolated(() => patchTranscriptInternal(t.id, { postprocess_status: "pending" }));
		}
	}
	const pending = (await listTranscriptsByPostprocess("pending")).slice(0, MAX_SUBMIT_PER_CYCLE);
	for (const t of pending) {
		await isolated(() => submitText(t, hub));
	}
	for (const t of active) {
		if (t.postprocess_job_id != null) {
			await isolated(() => pollText(t, hub));
		}
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
	await runTextJobs(hub);
}
