/**
 * Client for the Resona hub's published HTTP contract (`contract/openapi.json`).
 *
 * `createHubClient(...).api` is a typed `openapi-fetch` client for every contract route;
 * the helpers cover what every consumer repeats: error bodies, uploads, job submission
 * with a reused `Idempotency-Key`, polling and chunked Markdown reads. Behaviour follows
 * the Python `resona_client.ResonaClient`.
 */
import createClient, { type Client } from "openapi-fetch";

import type { components, paths } from "./schema.js";

export type Schemas = components["schemas"];
export type JobView = Schemas["JobView"];
export type JobAccepted = Schemas["JobAccepted"];
export type JobStatus = JobView["status"];
export type UploadView = Schemas["UploadView"];
export type MarkdownChunk = Schemas["MarkdownChunk"];
export type JobResult = Schemas["JobResult"];

export type JobKind = "audio" | "document" | "text";

/** Body of `POST /v1/jobs` (the route reads it unvalidated by OpenAPI, so it is written here). */
export interface JobRequest {
  kind: JobKind;
  upload_id: string;
  execution_profile?: string;
  language?: string;
  engine?: string;
  model?: string;
  /** Stored postprocess profile name; required for `kind: "text"`. */
  profile?: string;
  /** User instructions for the profile's llm/extract steps. */
  instructions?: string;
  /** `AudioOptions`, `DocumentOptions` or none for text jobs. */
  options?: Record<string, unknown>;
  /** Default `true` on the hub. */
  private?: boolean;
  provenance?: { project_id?: string; session_id?: string; source?: string };
  /** Name of an unrevoked webhook endpoint of the organization. */
  webhook?: string;
}

/** A job request that uploads `file` first and fills in `upload_id`. */
export type JobRequestWithFile = Omit<JobRequest, "upload_id"> & {
  file: Blob;
  filename?: string;
};

export const TERMINAL_STATUSES: ReadonlySet<JobStatus> = new Set(["completed", "failed", "cancelled"]);
/** Largest range `GET /v1/jobs/{id}/result/markdown` serves. */
export const MAX_MARKDOWN_CHUNK_CHARS = 32768;

const POST_JOB_ATTEMPTS = 3;
const RETRYABLE_STATUSES = new Set([502, 503, 504]);

/** A non-2xx hub response: `code` is the machine-readable `error` of the body (null if absent). */
export class HubError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(status: number, code: string | null, message: string) {
    super(message || (code ?? `HTTP ${status}`));
    this.name = "HubError";
    this.status = status;
    this.code = code;
  }

  /** Build from a response: `{error, message}` or FastAPI `{detail: ...}`. */
  static async fromResponse(response: Response): Promise<HubError> {
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      return new HubError(response.status, null, response.statusText);
    }
    return HubError.fromBody(response.status, body);
  }

  static fromBody(status: number, body: unknown): HubError {
    if (body === null || typeof body !== "object") return new HubError(status, null, "");
    const detail: unknown = "detail" in body ? body.detail : body;
    if (typeof detail === "string") return new HubError(status, null, detail);
    if (detail === null || typeof detail !== "object") return new HubError(status, null, "");
    const error = "error" in detail ? detail.error : undefined;
    const message = "message" in detail ? detail.message : undefined;
    return new HubError(
      status,
      typeof error === "string" ? error : null,
      typeof message === "string" ? message : "",
    );
  }
}

export interface HubClientOptions {
  /** Hub origin, e.g. `https://hub.example.org` (no trailing `/v1`). */
  baseUrl: string;
  /** Client or org-admin credential, sent as `Authorization: Bearer`. */
  token: string;
  /** Fetch implementation; defaults to the global `fetch`. */
  fetch?: (input: Request) => Promise<Response>;
  /** Delay before retry `attempt` (1, 2) of `POST /v1/jobs`; default 0.5 s · 2^attempt, at most 4 s. */
  retryDelayMs?: (attempt: number) => number;
}

export interface WaitOptions {
  signal?: AbortSignal;
  /** Poll interval, default 1000 ms. */
  pollMs?: number;
}

export interface HubClient {
  /** Typed `openapi-fetch` client for every route of the contract. */
  readonly api: Client<paths>;
  /** `POST /v1/uploads`: one file for one later job. */
  upload(file: Blob, filename?: string, init?: { signal?: AbortSignal }): Promise<UploadView>;
  /**
   * `POST /v1/jobs` (after `upload` when `file` is given). The `Idempotency-Key` is generated
   * once per call (or taken from `idempotencyKey`) and reused when the POST is retried after a
   * transport error or 502–504, so a lost response cannot create a second job.
   */
  submitJob(
    request: JobRequest | JobRequestWithFile,
    init?: { idempotencyKey?: string; signal?: AbortSignal },
  ): Promise<JobAccepted>;
  /** `GET /v1/jobs/{id}`. */
  getJob(jobId: number, init?: { signal?: AbortSignal }): Promise<JobView>;
  /** Poll `GET /v1/jobs/{id}` until the job is completed, failed or cancelled. */
  waitForJob(jobId: number, options?: WaitOptions): Promise<JobView>;
  /**
   * The result's whole Markdown, read in ranges of `GET /v1/jobs/{id}/result/markdown`.
   * `source: "raw"` reads `raw_markdown` (before postprocessing) when the result has one.
   */
  resultMarkdown(
    jobId: number,
    options?: { source?: "markdown" | "raw"; signal?: AbortSignal },
  ): Promise<string>;
}

function defaultRetryDelay(attempt: number): number {
  return Math.min(500 * 2 ** attempt, 4000);
}

// Executor form, not Promise.withResolvers: that needs Node 22, the package supports Node 20.
function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason);
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal?.reason);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export function createHubClient(options: HubClientOptions): HubClient {
  const baseUrl = options.baseUrl.replace(/\/+$/, "");
  const fetchImpl = options.fetch ?? ((input: Request) => globalThis.fetch(input));
  const retryDelay = options.retryDelayMs ?? defaultRetryDelay;
  const authorization = `Bearer ${options.token}`;

  const api = createClient<paths>({
    baseUrl,
    fetch: fetchImpl,
    headers: { Authorization: authorization },
  });

  function request(path: string, init: RequestInit): Promise<Response> {
    const headers = new Headers(init.headers);
    headers.set("Authorization", authorization);
    return fetchImpl(new Request(`${baseUrl}${path}`, { ...init, headers }));
  }

  async function json<T>(response: Response): Promise<T> {
    if (!response.ok) throw await HubError.fromResponse(response);
    return (await response.json()) as T;
  }

  async function upload(file: Blob, filename?: string, init?: { signal?: AbortSignal }) {
    const form = new FormData();
    const name = filename ?? (typeof File !== "undefined" && file instanceof File ? file.name : "upload");
    form.append("file", file, name);
    return json<UploadView>(await request("/v1/uploads", { method: "POST", body: form, signal: init?.signal }));
  }

  async function postJob(body: JobRequest, key: string, signal?: AbortSignal): Promise<JobAccepted> {
    const payload = JSON.stringify(body);
    let lastError: unknown;
    for (let attempt = 0; attempt < POST_JOB_ATTEMPTS; attempt++) {
      if (attempt) await sleep(retryDelay(attempt), signal);
      let response: Response;
      try {
        response = await request("/v1/jobs", {
          method: "POST",
          body: payload,
          headers: { "Content-Type": "application/json", "Idempotency-Key": key },
          signal,
        });
      } catch (error) {
        // An abort is the caller's decision, not a transport failure: never retried.
        if (signal?.aborted || (error instanceof Error && error.name === "AbortError")) throw error;
        lastError = error;
        continue;
      }
      if (RETRYABLE_STATUSES.has(response.status) && attempt < POST_JOB_ATTEMPTS - 1) {
        lastError = await HubError.fromResponse(response);
        continue;
      }
      return json<JobAccepted>(response);
    }
    throw lastError;
  }

  async function submitJob(
    req: JobRequest | JobRequestWithFile,
    init?: { idempotencyKey?: string; signal?: AbortSignal },
  ): Promise<JobAccepted> {
    const key = init?.idempotencyKey ?? crypto.randomUUID();
    let body: JobRequest;
    if ("file" in req) {
      const { file, filename, ...rest } = req;
      const uploaded = await upload(file, filename, { signal: init?.signal });
      body = { ...rest, upload_id: uploaded.upload_id };
    } else {
      body = req;
    }
    return postJob(body, key, init?.signal);
  }

  async function getJob(jobId: number, init?: { signal?: AbortSignal }): Promise<JobView> {
    const { data, error, response } = await api.GET("/v1/jobs/{job_id}", {
      params: { path: { job_id: jobId } },
      signal: init?.signal,
    });
    if (data === undefined) throw HubError.fromBody(response.status, error);
    return data;
  }

  async function waitForJob(jobId: number, opts: WaitOptions = {}): Promise<JobView> {
    const pollMs = opts.pollMs ?? 1000;
    for (;;) {
      const job = await getJob(jobId, { signal: opts.signal });
      if (TERMINAL_STATUSES.has(job.status)) return job;
      await sleep(pollMs, opts.signal);
    }
  }

  async function resultMarkdown(
    jobId: number,
    opts: { source?: "markdown" | "raw"; signal?: AbortSignal } = {},
  ): Promise<string> {
    const parts: string[] = [];
    let offset: number | null = 0;
    while (offset !== null) {
      const { data, error, response } = await api.GET("/v1/jobs/{job_id}/result/markdown", {
        params: {
          path: { job_id: jobId },
          query: { offset, limit: MAX_MARKDOWN_CHUNK_CHARS, source: opts.source ?? "markdown" },
        },
        signal: opts.signal,
      });
      if (data === undefined) throw HubError.fromBody(response.status, error);
      const chunk: MarkdownChunk = data;
      parts.push(chunk.text);
      offset = chunk.next_offset ?? null;
    }
    return parts.join("");
  }

  return { api, upload, submitJob, getJob, waitForJob, resultMarkdown };
}
