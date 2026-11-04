import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { HubError } from "~/composables/useHub";
import { runLocalCycle } from "~/data/localProcessor";

type Row = Record<string, unknown> & { id: string; status: string; date_created: string };

const db = vi.hoisted(() => ({
	recordings: new Map<string, Record<string, unknown>>(),
	documents: new Map<string, Record<string, unknown>>(),
	files: new Map<string, Blob>(),
	transcripts: new Map<string, Record<string, unknown>>(),
}));

vi.mock("~/data/localStore", () => ({
	listByStatus: vi.fn(async (collection: "recordings" | "documents", status: string) =>
		[...db[collection].values()]
			.filter(r => r.status === status)
			.sort((a, b) => String(a.date_created).localeCompare(String(b.date_created)))
			.map(r => ({ ...r }))),
	readFile: vi.fn(async (id: string) => db.files.get(id) ?? null),
	patchRecordingInternal: vi.fn(async (id: string, patch: Record<string, unknown>) => {
		db.recordings.set(id, { ...db.recordings.get(id), ...patch });
	}),
	patchDocumentInternal: vi.fn(async (id: string, patch: Record<string, unknown>) => {
		db.documents.set(id, { ...db.documents.get(id), ...patch });
	}),
	upsertTranscript: vi.fn(async (recordingId: string, t: Record<string, unknown>) => {
		db.transcripts.set(recordingId, { recording: recordingId, ...t });
	}),
}));

const hub = vi.hoisted(() => ({
	hasToken: vi.fn(() => true),
	upload: vi.fn(async (_blob: Blob, _name: string) => "up-1"),
	createJob: vi.fn(async (_body: Record<string, unknown>, _key: string) => 42),
	getJob: vi.fn(),
	getResult: vi.fn(),
	deleteJob: vi.fn(async (_id: number) => {}),
}));

mockNuxtImport("useHub", () => () => hub);

function recording(id: string, extra: Partial<Row> = {}): Row {
	const row: Row = {
		id,
		title: id,
		audio_file: `f-${id}`,
		language: "de",
		profile: "medical",
		status: "pending",
		source: "batch",
		hub_job_id: null,
		date_created: `2026-01-01T00:00:${id.padStart(2, "0")}Z`,
		...extra,
	};
	db.recordings.set(id, row);
	return row;
}

function doc(id: string, extra: Partial<Row> = {}): Row {
	const row: Row = {
		id,
		title: id,
		file: `f-${id}`,
		status: "pending",
		hub_job_id: null,
		date_created: `2026-01-01T00:00:${id.padStart(2, "0")}Z`,
		...extra,
	};
	db.documents.set(id, row);
	return row;
}

const RESULT = {
	markdown: "# Hallo",
	raw_text: "Hallo",
	raw_markdown: "Hallo",
	language: "de",
	segments: [{ start: 0, end: 1, text: "Hallo" }],
	pages: [{ n: 1 }],
	warnings: [],
	structured: { a: 1 },
	provenance: null,
};

function job(status: string, extra: Record<string, unknown> = {}) {
	return { status, error: null, candidate: null, queue_reason: null, ...extra };
}

describe("runLocalCycle", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		db.recordings.clear();
		db.documents.clear();
		db.files.clear();
		db.transcripts.clear();
		hub.hasToken.mockReturnValue(true);
		hub.upload.mockResolvedValue("up-1");
		hub.createJob.mockResolvedValue(42);
		hub.deleteJob.mockResolvedValue(undefined);
	});

	it("does nothing without a hub token", async () => {
		hub.hasToken.mockReturnValue(false);
		recording("1");
		db.files.set("f-1", new Blob(["a"]));
		await runLocalCycle();
		expect(hub.upload).not.toHaveBeenCalled();
		expect(hub.createJob).not.toHaveBeenCalled();
		expect(db.recordings.get("1")!.status).toBe("pending");
	});

	it("submits a pending recording and marks it transcribing with the job id", async () => {
		recording("1");
		db.files.set("f-1", new File(["audio"], "talk.webm", { type: "audio/webm" }));
		await runLocalCycle();
		expect(hub.upload).toHaveBeenCalledWith(expect.any(Blob), "talk.webm");
		const [body, key] = hub.createJob.mock.calls[0]!;
		expect(body).toEqual({ kind: "audio", upload_id: "up-1", language: "de", profile: "medical" });
		expect(key).toMatch(/^local:recordings:1:.+/);
		expect(db.recordings.get("1")).toMatchObject({ status: "transcribing", hub_job_id: 42, error_message: null });
	});

	it("uses a fallback upload name when the stored blob has none", async () => {
		recording("1");
		db.files.set("f-1", new Blob(["audio"]));
		await runLocalCycle();
		expect(hub.upload).toHaveBeenCalledWith(expect.any(Blob), "1.webm");
	});

	it("uses a fresh idempotency key per submit", async () => {
		recording("1");
		recording("2");
		db.files.set("f-1", new Blob(["a"]));
		db.files.set("f-2", new Blob(["b"]));
		await runLocalCycle();
		const keys = hub.createJob.mock.calls.map(c => c[1]);
		expect(keys[0]).not.toBe(keys[1]);
	});

	it("persists the idempotency key on the row before creating the job", async () => {
		recording("1");
		db.files.set("f-1", new Blob(["a"]));
		const seen: unknown[] = [];
		hub.createJob.mockImplementation(async (_body, key) => {
			seen.push(db.recordings.get("1")!.hub_job_key === key);
			throw new HubError(0, "timeout", "timeout");
		});
		await runLocalCycle();
		expect(seen).toEqual([true]);
		expect(db.recordings.get("1")).toMatchObject({ status: "pending", hub_job_id: null });
		expect(db.recordings.get("1")!.hub_job_key).toMatch(/^local:recordings:1:.+/);
	});

	it("resubmits with the row's existing idempotency key", async () => {
		recording("1", { hub_job_key: "local:recordings:1:first" });
		db.files.set("f-1", new Blob(["a"]));
		await runLocalCycle();
		expect(hub.createJob.mock.calls[0]![1]).toBe("local:recordings:1:first");
		expect(db.recordings.get("1")).toMatchObject({ status: "transcribing", hub_job_id: 42, hub_job_key: "local:recordings:1:first" });
	});

	it("clears the idempotency key when a submit fails", async () => {
		recording("1", { hub_job_key: "local:recordings:1:first" });
		db.files.set("f-1", new Blob(["a"]));
		hub.createJob.mockRejectedValue(new HubError(400, "bad_request", "bad"));
		await runLocalCycle();
		expect(db.recordings.get("1")).toMatchObject({ status: "error", hub_job_id: null, hub_job_key: null });
	});

	it("sends only kind and upload_id for documents", async () => {
		doc("1");
		db.files.set("f-1", new Blob(["%PDF"]));
		await runLocalCycle();
		expect(hub.upload).toHaveBeenCalledWith(expect.any(Blob), "1.bin");
		const [body, key] = hub.createJob.mock.calls[0]!;
		expect(body).toEqual({ kind: "document", upload_id: "up-1" });
		expect(key).toMatch(/^local:documents:1:.+/);
		expect(db.documents.get("1")).toMatchObject({ status: "processing", hub_job_id: 42 });
	});

	it("marks a row whose file is missing as error 'Datei fehlt'", async () => {
		recording("1");
		await runLocalCycle();
		expect(hub.upload).not.toHaveBeenCalled();
		expect(db.recordings.get("1")).toMatchObject({ status: "error", error_message: "Datei fehlt" });
	});

	it("writes the transcript from the result markdown, marks done and deletes the hub job", async () => {
		recording("1", { status: "transcribing", hub_job_id: 7 });
		hub.getJob.mockResolvedValue(job("completed", { candidate: { engine: "whisper", model: "large" } }));
		hub.getResult.mockResolvedValue(RESULT);
		await runLocalCycle();
		expect(hub.getJob).toHaveBeenCalledWith(7);
		expect(db.transcripts.get("1")).toMatchObject({
			text: "# Hallo",
			segments: RESULT.segments,
			structured: RESULT.structured,
			engine: "whisper",
		});
		expect(db.recordings.get("1")).toMatchObject({ status: "done", hub_job_id: null });
		expect(hub.deleteJob).toHaveBeenCalledWith(7);
	});

	it("stores markdown and pages on a completed document", async () => {
		doc("1", { status: "processing", hub_job_id: 8 });
		hub.getJob.mockResolvedValue(job("completed"));
		hub.getResult.mockResolvedValue(RESULT);
		await runLocalCycle();
		expect(db.documents.get("1")).toMatchObject({ status: "done", hub_job_id: null, markdown: "# Hallo", pages: RESULT.pages });
		expect(db.transcripts.size).toBe(0);
	});

	it("ignores deleteJob errors after completing", async () => {
		recording("1", { status: "transcribing", hub_job_id: 7 });
		hub.getJob.mockResolvedValue(job("completed"));
		hub.getResult.mockResolvedValue(RESULT);
		hub.deleteJob.mockRejectedValue(new HubError(500, "http_500", "boom"));
		await runLocalCycle();
		expect(db.recordings.get("1")).toMatchObject({ status: "done", hub_job_id: null });
	});

	it("maps a failed job to its error message", async () => {
		recording("1", { status: "transcribing", hub_job_id: 7 });
		hub.getJob.mockResolvedValue(job("failed", { error: { code: "engine_down", message: "Engine weg" } }));
		await runLocalCycle();
		expect(db.recordings.get("1")).toMatchObject({ status: "error", error_message: "Verarbeitung fehlgeschlagen: Engine weg" });
	});

	it("falls back to the job status for a cancelled job without error", async () => {
		recording("1", { status: "transcribing", hub_job_id: 7 });
		hub.getJob.mockResolvedValue(job("cancelled"));
		await runLocalCycle();
		expect(db.recordings.get("1")!.error_message).toBe("Verarbeitung fehlgeschlagen: cancelled");
	});

	it("maps a 404 job to 'Hub-Job nicht gefunden'", async () => {
		recording("1", { status: "transcribing", hub_job_id: 7 });
		hub.getJob.mockRejectedValue(new HubError(404, "not_found", "nope"));
		await runLocalCycle();
		expect(db.recordings.get("1")).toMatchObject({ status: "error", error_message: "Hub-Job nicht gefunden" });
	});

	it("maps an expired result (410) to 'Ergebnis auf dem Hub abgelaufen'", async () => {
		recording("1", { status: "transcribing", hub_job_id: 7 });
		hub.getJob.mockResolvedValue(job("completed"));
		hub.getResult.mockRejectedValue(new HubError(410, "gone", "expired"));
		await runLocalCycle();
		expect(db.recordings.get("1")).toMatchObject({ status: "error", error_message: "Ergebnis auf dem Hub abgelaufen" });
		expect(db.transcripts.size).toBe(0);
	});

	it("leaves rows unchanged on network (status 0) errors", async () => {
		recording("1", { status: "transcribing", hub_job_id: 7 });
		recording("2");
		db.files.set("f-2", new Blob(["a"]));
		hub.getJob.mockRejectedValue(new HubError(0, "network", "offline"));
		hub.upload.mockRejectedValue(new HubError(0, "network", "offline"));
		await runLocalCycle();
		expect(db.recordings.get("1")).toMatchObject({ status: "transcribing", hub_job_id: 7 });
		expect(db.recordings.get("2")).toMatchObject({ status: "pending", hub_job_id: null });
	});

	it("leaves rows unchanged on transient hub errors (429/502/503/504)", async () => {
		for (const status of [429, 502, 503, 504]) {
			db.recordings.clear();
			recording("1", { status: "transcribing", hub_job_id: 7 });
			recording("2");
			db.files.set("f-2", new Blob(["a"]));
			hub.getJob.mockRejectedValue(new HubError(status, `http_${status}`, "busy"));
			hub.upload.mockRejectedValue(new HubError(status, `http_${status}`, "busy"));
			await runLocalCycle();
			expect(db.recordings.get("1")).toMatchObject({ status: "transcribing", hub_job_id: 7 });
			expect(db.recordings.get("2")).toMatchObject({ status: "pending", hub_job_id: null });
		}
	});

	it("marks a row error with the message on a non-transient getJob error", async () => {
		recording("1", { status: "transcribing", hub_job_id: 7 });
		hub.getJob.mockRejectedValue(new HubError(500, "http_500", "boom"));
		await runLocalCycle();
		expect(db.recordings.get("1")).toMatchObject({ status: "error", error_message: "boom", hub_job_id: null });
	});

	it("marks a row error with the message on a non-transient getResult error", async () => {
		recording("1", { status: "transcribing", hub_job_id: 7 });
		hub.getJob.mockResolvedValue(job("completed"));
		hub.getResult.mockRejectedValue(new HubError(500, "http_500", "boom"));
		await runLocalCycle();
		expect(db.recordings.get("1")).toMatchObject({ status: "error", error_message: "boom", hub_job_id: null });
	});

	it("stores a non-transient submit error message without prefix", async () => {
		recording("1");
		db.files.set("f-1", new Blob(["a"]));
		hub.upload.mockRejectedValue(new HubError(413, "too_large", "Datei zu groß"));
		await runLocalCycle();
		expect(db.recordings.get("1")).toMatchObject({ status: "error", error_message: "Datei zu groß" });
	});

	it("keeps pending/processing jobs unchanged", async () => {
		recording("1", { status: "transcribing", hub_job_id: 7 });
		hub.getJob.mockResolvedValue(job("processing"));
		await runLocalCycle();
		expect(hub.getResult).not.toHaveBeenCalled();
		expect(db.recordings.get("1")).toMatchObject({ status: "transcribing", hub_job_id: 7 });
	});

	it("resets an active row without a job id back to pending", async () => {
		doc("1", { status: "processing", hub_job_id: null });
		db.files.set("f-1", new Blob(["%PDF"]));
		hub.upload.mockRejectedValue(new HubError(0, "network", "offline"));
		await runLocalCycle();
		expect(hub.getJob).not.toHaveBeenCalled();
		expect(db.documents.get("1")!.status).toBe("pending");
	});

	it("isolates per-row failures", async () => {
		recording("1", { status: "transcribing", hub_job_id: 7 });
		recording("2", { status: "transcribing", hub_job_id: 8 });
		hub.getJob.mockImplementation(async (id: number) => {
			if (id === 7) throw new Error("boom");
			return job("failed", { error: { code: "x", message: "kaputt" } });
		});
		await runLocalCycle();
		expect(db.recordings.get("1")).toMatchObject({ status: "transcribing", hub_job_id: 7 });
		expect(db.recordings.get("2")!.error_message).toBe("Verarbeitung fehlgeschlagen: kaputt");
	});

	it("submits at most 5 pending rows per collection per cycle, oldest first", async () => {
		for (let i = 1; i <= 7; i++) {
			recording(String(i));
			db.files.set(`f-${i}`, new Blob([String(i)]));
		}
		await runLocalCycle();
		expect(hub.createJob).toHaveBeenCalledTimes(5);
		const submitted = hub.createJob.mock.calls.map(c => String(c[1]).split(":")[2]);
		expect(submitted).toEqual(["1", "2", "3", "4", "5"]);
		expect(db.recordings.get("6")!.status).toBe("pending");
		expect(db.recordings.get("7")!.status).toBe("pending");
	});
});
