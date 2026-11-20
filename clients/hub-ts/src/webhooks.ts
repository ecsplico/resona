/**
 * Verify the hub's job webhooks (Standard Webhooks) with WebCrypto, matching
 * `resona_job_core.webhooks.sign`: `v1,` + base64 HMAC-SHA256 over
 * `<webhook-id>.<webhook-timestamp>.<body>`, keyed with the base64-decoded part of a
 * `whsec_` secret (any other secret: its UTF-8 bytes).
 */

export const SECRET_PREFIX = "whsec_";
export const SIGNATURE_VERSION = "v1";
/** Accepted distance between `webhook-timestamp` and now, in seconds. */
export const DEFAULT_TOLERANCE_SECONDS = 300;

/** Payload of a job webhook: identifiers, enums and a time only. */
export interface JobEvent {
  type: "job.completed" | "job.failed" | "job.cancelled";
  job_id: number;
  kind: "audio" | "document" | "text";
  status: "completed" | "failed" | "cancelled";
  error_code: string | null;
  finished_at: string;
}

export class WebhookVerificationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WebhookVerificationError";
  }
}

/** `Headers`, or a plain header map such as Node's `IncomingHttpHeaders`. */
export type WebhookHeaders = Headers | Record<string, string | string[] | undefined>;

export interface VerifyOptions {
  toleranceSeconds?: number;
  /** Current Unix time in seconds; defaults to the clock. */
  now?: number;
}

const BASE64 = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

// `Uint8Array<ArrayBuffer>`: WebCrypto's BufferSource excludes views on a SharedArrayBuffer.
function base64Bytes(text: string): Uint8Array<ArrayBuffer> | null {
  if (!BASE64.test(text)) return null;
  return Uint8Array.from(atob(text), (c) => c.charCodeAt(0));
}

/** HMAC key bytes of a signing secret, as `resona_job_core.webhooks.secret_key`. */
export function secretKey(secret: string): Uint8Array<ArrayBuffer> {
  if (!secret.startsWith(SECRET_PREFIX)) return new TextEncoder().encode(secret);
  const key = base64Bytes(secret.slice(SECRET_PREFIX.length));
  if (key === null) throw new Error("webhook secret is not valid base64");
  return key;
}

function header(headers: WebhookHeaders, name: string): string | null {
  if (headers instanceof Headers) return headers.get(name);
  const entry = Object.entries(headers).find(([key]) => key.toLowerCase() === name)?.[1];
  return Array.isArray(entry) ? (entry[0] ?? null) : (entry ?? null);
}

/**
 * Check the signature and timestamp of one delivery and return its parsed event.
 * `body` must be the exact request bytes (or their UTF-8 text), not re-serialized JSON.
 * Throws `WebhookVerificationError` when a header is missing, the timestamp is outside the
 * tolerance or no `v1` signature matches.
 */
export async function verifyWebhook(
  headers: WebhookHeaders,
  body: string | Uint8Array | ArrayBuffer,
  secret: string,
  options: VerifyOptions = {},
): Promise<JobEvent> {
  const id = header(headers, "webhook-id");
  const timestamp = header(headers, "webhook-timestamp");
  const signatures = header(headers, "webhook-signature");
  if (!id || !timestamp || !signatures) throw new WebhookVerificationError("missing webhook headers");
  if (!/^\d+$/.test(timestamp)) throw new WebhookVerificationError("invalid webhook-timestamp");
  const now = options.now ?? Math.floor(Date.now() / 1000);
  const tolerance = options.toleranceSeconds ?? DEFAULT_TOLERANCE_SECONDS;
  if (Math.abs(now - Number(timestamp)) > tolerance) {
    throw new WebhookVerificationError("webhook-timestamp outside the tolerance");
  }

  const bodyBytes =
    typeof body === "string" ? new TextEncoder().encode(body) : body instanceof Uint8Array ? body : new Uint8Array(body);
  const prefix = new TextEncoder().encode(`${id}.${timestamp}.`);
  const content = new Uint8Array(prefix.length + bodyBytes.length);
  content.set(prefix);
  content.set(bodyBytes, prefix.length);

  const key = await crypto.subtle.importKey("raw", secretKey(secret), { name: "HMAC", hash: "SHA-256" }, false, [
    "verify",
  ]);
  for (const entry of signatures.split(" ")) {
    const [version, signature] = entry.split(",", 2);
    if (version !== SIGNATURE_VERSION || !signature) continue;
    const expected = base64Bytes(signature);
    // subtle.verify compares in constant time.
    if (expected && (await crypto.subtle.verify("HMAC", key, expected, content))) {
      return JSON.parse(new TextDecoder().decode(bodyBytes)) as JobEvent;
    }
  }
  throw new WebhookVerificationError("no matching webhook signature");
}
