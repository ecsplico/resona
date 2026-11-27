import { describe, expect, it } from "vitest";

import { createHubClient, HubError, SyncWaitTimeout, type JobView } from "../src/index.js";

type Handler = (request: Request) => Response | Promise<Response>;

/** A fetch that answers with `handlers` in order and records every request. */
function mockFetch(...handlers: Handler[]) {
  const requests: Request[] = [];
  const fetch = async (request: Request) => {
    requests.push(request.clone());
    const handler = handlers.shift();
    if (!handler) throw new Error(`unexpected request ${request.method} ${request.url}`);
    return handler(request);
  };
  return { fetch, requests };
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const job = (status: JobView["status"]): JobView =>
  ({ job_id: 7, kind: "audio", status, private: true, attempt: 0 }) as JobView;

function client(fetch: (request: Request) => Promise<Response>) {
  return createHubClient({ baseUrl: "https://hub.example/", token: "tok", fetch, retryDelayMs: () => 0 });
}

describe("submitJob", () => {
  it("reuses the Idempotency-Key when retrying after a 503", async () => {
    const mock = mockFetch(
      () => json(503, { error: "unavailable", message: "busy" }),
      () => json(202, { job_id: 7, status: "pending" }),
    );
    const accepted = await client(mock.fetch).submitJob({ kind: "audio", upload_id: "up-1" });

    expect(accepted).toEqual({ job_id: 7, status: "pending" });
    expect(mock.requests).toHaveLength(2);
    const keys = mock.requests.map((r) => r.headers.get("Idempotency-Key"));
    expect(keys[0]).toMatch(/^[0-9a-f-]{36}$/);
    expect(keys[1]).toBe(keys[0]);
    for (const request of mock.requests) {
      expect(request.method).toBe("POST");
      expect(request.url).toBe("https://hub.example/v1/jobs");
      expect(request.headers.get("Authorization")).toBe("Bearer tok");
      expect(await request.json()).toEqual({ kind: "audio", upload_id: "up-1" });
    }
  });

  it("reuses the key after a transport error and honours a given key", async () => {
    const mock = mockFetch(
      () => {
        throw new TypeError("fetch failed");
      },
      () => json(202, { job_id: 8, status: "pending" }),
    );
    await client(mock.fetch).submitJob({ kind: "text", upload_id: "up-2", profile: "p" }, { idempotencyKey: "k-1" });
    expect(mock.requests.map((r) => r.headers.get("Idempotency-Key"))).toEqual(["k-1", "k-1"]);
  });

  it("gives up after three attempts with the last HubError", async () => {
    const mock = mockFetch(
      () => json(502, { error: "bad_gateway", message: "a" }),
      () => json(504, { error: "timeout", message: "b" }),
      () => json(503, { error: "unavailable", message: "c" }),
    );
    const error = await client(mock.fetch).submitJob({ kind: "audio", upload_id: "u" }).catch((e) => e);
    expect(error).toBeInstanceOf(HubError);
    expect(error).toMatchObject({ status: 503, code: "unavailable", message: "c" });
    expect(new Set(mock.requests.map((r) => r.headers.get("Idempotency-Key"))).size).toBe(1);
  });

  it("does not retry a 4xx", async () => {
    const mock = mockFetch(() => json(403, { error: "policy_denied", message: "cloud disabled" }));
    const error = await client(mock.fetch).submitJob({ kind: "audio", upload_id: "u" }).catch((e) => e);
    expect(error).toMatchObject({ status: 403, code: "policy_denied", message: "cloud disabled" });
    expect(mock.requests).toHaveLength(1);
  });

  it("uploads a file first and submits its upload_id", async () => {
    const mock = mockFetch(
      () => json(201, { upload_id: "up-9", sha256: "00", size: 3 }),
      () => json(202, { job_id: 9, status: "pending" }),
    );
    const file = new Blob([new Uint8Array([1, 2, 3])], { type: "audio/wav" });
    await client(mock.fetch).submitJob({ kind: "audio", file, filename: "memo.wav", language: "de" });

    const [upload, submit] = mock.requests;
    expect(upload?.url).toBe("https://hub.example/v1/uploads");
    const form = await upload!.formData();
    const sent = form.get("file") as File;
    expect(sent.name).toBe("memo.wav");
    expect(new Uint8Array(await sent.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3]));
    expect(await submit!.json()).toEqual({ kind: "audio", language: "de", upload_id: "up-9" });
  });

  it("sends the insertion context with the job", async () => {
    const mock = mockFetch(() => json(202, { job_id: 9, status: "pending" }));
    const context = { before: "Im Befund steht:", after: "Weiter im Text." };
    await client(mock.fetch).submitJob({ kind: "text", upload_id: "up-9", profile: "cleanup", context });

    expect(await mock.requests[0]!.json()).toEqual({
      kind: "text", upload_id: "up-9", profile: "cleanup", context,
    });
  });
});

describe("HubError", () => {
  it("reads {error, message} and FastAPI detail bodies", () => {
    expect(HubError.fromBody(409, { error: "not_ready", message: "later" })).toMatchObject({
      status: 409,
      code: "not_ready",
      message: "later",
    });
    expect(HubError.fromBody(422, { detail: { error: "invalid_request", message: "x" } }).code).toBe(
      "invalid_request",
    );
    expect(HubError.fromBody(404, { detail: "Not Found" })).toMatchObject({ code: null, message: "Not Found" });
  });

  it("is thrown by getJob with the hub code", async () => {
    const mock = mockFetch(() => json(404, { error: "job_not_found", message: "job not found" }));
    const error = await client(mock.fetch).getJob(1).catch((e) => e);
    expect(error).toBeInstanceOf(HubError);
    expect(error).toMatchObject({ status: 404, code: "job_not_found" });
  });
});

describe("waitForJob", () => {
  it("polls until the job is terminal", async () => {
    const mock = mockFetch(
      () => json(200, job("pending")),
      () => json(200, job("processing")),
      () => json(200, job("completed")),
    );
    const done = await client(mock.fetch).waitForJob(7, { pollMs: 0 });
    expect(done.status).toBe("completed");
    expect(mock.requests.map((r) => r.url)).toEqual(Array(3).fill("https://hub.example/v1/jobs/7"));
    expect(mock.requests[0]?.headers.get("Authorization")).toBe("Bearer tok");
  });

  it("stops when the signal aborts", async () => {
    const controller = new AbortController();
    const mock = mockFetch(() => {
      controller.abort(new Error("stop"));
      return json(200, job("pending"));
    });
    const waiting = client(mock.fetch).waitForJob(7, { pollMs: 60_000, signal: controller.signal });
    await expect(waiting).rejects.toThrow("stop");
    expect(mock.requests).toHaveLength(1);
  });
});

describe("resultMarkdown", () => {
  it("joins the range reads", async () => {
    const chunk = (text: string, offset: number, next: number | null) =>
      json(200, { text, offset, next_offset: next, total_chars: 6, source: "markdown", kind: "audio", warnings: [] });
    const mock = mockFetch(() => chunk("abc", 0, 3), () => chunk("def", 3, null));
    expect(await client(mock.fetch).resultMarkdown(7, { source: "raw" })).toBe("abcdef");
    const params = mock.requests.map((r) => Object.fromEntries(new URL(r.url).searchParams));
    expect(params).toEqual([
      { offset: "0", limit: "32768", source: "raw" },
      { offset: "3", limit: "32768", source: "raw" },
    ]);
  });

  it("throws the hub error of a job that is not done", async () => {
    const mock = mockFetch(() => json(409, { error: "not_ready", message: "job is processing" }));
    await expect(client(mock.fetch).resultMarkdown(7)).rejects.toMatchObject({ status: 409, code: "not_ready" });
  });
});

describe("listenPrerecorded", () => {
  it("posts the audio as the raw body with Deepgram query params", async () => {
    const answer = {
      metadata: { request_id: "7", duration: 1, channels: 1, model_info: { engine: "faster-whisper" } },
      results: { channels: [{ detected_language: "de", alternatives: [{ transcript: "hallo", words: [] }] }] },
    };
    const mock = mockFetch(() => json(200, answer));
    const audio = new Blob([new Uint8Array([1, 2, 3])], { type: "audio/webm" });
    const result = await client(mock.fetch).listenPrerecorded(audio, {
      language: "de", model: "nova-3", private: false, idempotencyKey: "k-1",
    });
    expect(result).toEqual(answer);
    const request = mock.requests[0]!;
    expect(request.method).toBe("POST");
    const url = new URL(request.url);
    expect(url.pathname).toBe("/v1/listen");
    expect(Object.fromEntries(url.searchParams)).toEqual({ language: "de", model: "nova-3", private: "false" });
    expect(request.headers.get("Content-Type")).toBe("audio/webm");
    expect(request.headers.get("Idempotency-Key")).toBe("k-1");
    expect(request.headers.get("Authorization")).toBe("Bearer tok");
    expect(new Uint8Array(await request.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3]));
  });

  it("throws SyncWaitTimeout with the running job on 504", async () => {
    const mock = mockFetch(() => json(504, { error: "timeout", message: "still running", job_id: 9 }));
    const error = await client(mock.fetch).listenPrerecorded(new Blob([new Uint8Array([0])])).catch((e) => e);
    expect(error).toBeInstanceOf(SyncWaitTimeout);
    expect(error).toMatchObject({ status: 504, code: "timeout", jobId: 9 });
  });

  it("throws the hub error otherwise", async () => {
    const mock = mockFetch(() => json(403, { error: "policy_denied", message: "no cloud" }));
    await expect(client(mock.fetch).listenPrerecorded(new Blob([]))).rejects.toMatchObject({
      status: 403, code: "policy_denied",
    });
  });
});
