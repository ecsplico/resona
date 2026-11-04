/**
 * The only hub HTTP client in the app. Every request authenticates with the
 * per-device token from `useHubSettings` (`Authorization: Bearer <token>`).
 */

export class HubError extends Error {
	/** HTTP status; 0 = network failure (unreachable host, CORS, offline). */
	status: number;
	/** Hub error code from the body's `error`, else `http_<status>`. */
	code: string;

	constructor(status: number, code: string, message: string) {
		super(message);
		this.name = "HubError";
		this.status = status;
		this.code = code;
	}
}

export interface HubEngine {
	name: string;
	kind: string;
	job_kind: string;
	private: boolean;
	available: boolean;
	models: string[];
	candidates: unknown[];
}

export interface ListenEngine {
	name: string;
	kind: string;
	private: boolean;
	models: string[];
}

export type HubJobStatus = "pending" | "processing" | "completed" | "failed" | "cancelled";

export interface HubJob {
	status: HubJobStatus;
	error: { code: string; message: string } | null;
	candidate: { engine: string; model: string; [key: string]: unknown } | null;
	queue_reason: string | null;
	[key: string]: unknown;
}

export interface HubSegment {
	start: number;
	end: number;
	text: string;
}

export interface HubResult {
	markdown: string;
	raw_text: string;
	raw_markdown: string;
	language: string | null;
	segments: HubSegment[];
	pages: unknown[];
	warnings: unknown[];
	structured: unknown;
	provenance: unknown;
}

export interface EngineList<T> {
	engines: T[];
	default: string | null;
}

export function useHub() {
	const cfg = useRuntimeConfig().public;
	const settings = useHubSettings();
	const base = trimUrl(cfg.resonaApiUrl);

	function token(): string {
		return settings.token;
	}

	function hasToken(): boolean {
		return token().length > 0;
	}

	function headers(): Record<string, string> {
		const t = token();
		return t ? { Authorization: `Bearer ${t}` } : {};
	}

	async function request(path: string, init: RequestInit = {}): Promise<Response> {
		let resp: Response;
		try {
			resp = await fetch(`${base}${path}`, {
				...init,
				headers: { ...headers(), ...(init.headers as Record<string, string> | undefined) },
			});
		}
		catch (err) {
			throw new HubError(0, "network", err instanceof Error ? err.message : String(err));
		}
		if (!resp.ok) {
			let body: { error?: unknown; message?: unknown } = {};
			try {
				body = await resp.json() as typeof body;
			}
			catch {
				// Non-JSON error body — fall back to the status line.
			}
			throw new HubError(
				resp.status,
				typeof body?.error === "string" ? body.error : `http_${resp.status}`,
				typeof body?.message === "string" ? body.message : resp.statusText,
			);
		}
		return resp;
	}

	async function json<T>(path: string, init?: RequestInit): Promise<T> {
		const resp = await request(path, init);
		return await resp.json() as T;
	}

	async function listProfiles(): Promise<string[]> {
		const body = await json<{ profiles?: { name?: unknown }[] }>("/profiles");
		return (Array.isArray(body.profiles) ? body.profiles : [])
			.map(p => p?.name)
			.filter((n): n is string => typeof n === "string" && n.length > 0);
	}

	async function listEngines(): Promise<EngineList<HubEngine>> {
		return await json<EngineList<HubEngine>>("/v1/engines");
	}

	async function listListenEngines(): Promise<EngineList<ListenEngine>> {
		try {
			return await json<EngineList<ListenEngine>>("/v1/listen/engines");
		}
		catch (err) {
			// A hub without the discovery route advertises nothing.
			if (err instanceof HubError && err.status === 404) {
				return { engines: [], default: null };
			}
			throw err;
		}
	}

	async function transcribeSync(
		blob: Blob,
		filename: string,
		fields: { language: string; engine?: string; profile?: string },
	): Promise<string> {
		const form = new FormData();
		form.append("file", blob, filename);
		form.append("language", fields.language);
		form.append("response_format", "json");
		if (fields.engine) form.append("engine", fields.engine);
		if (fields.profile) form.append("profile", fields.profile);
		try {
			const body = await json<{ text?: string }>("/v1/audio/transcriptions", {
				method: "POST",
				headers: { "Idempotency-Key": crypto.randomUUID() },
				body: form,
			});
			return body.text ?? "";
		}
		catch (err) {
			// The hub waits ≤60 s, then 504s while the job keeps running.
			if (err instanceof HubError && err.status === 504) {
				throw new HubError(504, "timeout", err.message);
			}
			throw err;
		}
	}

	async function upload(blob: Blob, filename: string): Promise<string> {
		const form = new FormData();
		form.append("file", blob, filename);
		const body = await json<{ upload_id: string }>("/v1/uploads", { method: "POST", body: form });
		return body.upload_id;
	}

	async function createJob(
		body: { kind: "audio" | "document"; upload_id: string; language?: string; profile?: string; engine?: string },
		idempotencyKey: string,
	): Promise<number> {
		const res = await json<{ job_id: number }>("/v1/jobs", {
			method: "POST",
			headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
			body: JSON.stringify({ ...body, private: true }),
		});
		return res.job_id;
	}

	async function getJob(id: number): Promise<HubJob> {
		return await json<HubJob>(`/v1/jobs/${id}`);
	}

	async function getResult(id: number): Promise<HubResult> {
		return await json<HubResult>(`/v1/jobs/${id}/result`);
	}

	async function deleteJob(id: number): Promise<void> {
		await request(`/v1/jobs/${id}`, { method: "DELETE" });
	}

	return {
		token,
		hasToken,
		headers,
		listProfiles,
		listEngines,
		listListenEngines,
		transcribeSync,
		upload,
		createJob,
		getJob,
		getResult,
		deleteJob,
	};
}
