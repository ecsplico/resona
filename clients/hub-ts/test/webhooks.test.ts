import { describe, expect, it } from "vitest";

import { secretKey, verifyWebhook, WebhookVerificationError } from "../src/index.js";

// Vectors from the Python signer:
//   resona_job_core.webhooks.sign(SECRET, MSG_ID, TIMESTAMP, BODY.encode())
//   resona_job_core.webhooks.sign("plain-secret", "msg_1", TIMESTAMP, BODY.encode())
const SECRET = "whsec_AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="; // base64 of bytes 0..31
const MSG_ID = "msg_2KWPBgLlAfxdpx2AI54pPJ85f4W";
const TIMESTAMP = 1763640000;
const BODY =
  '{"type":"job.completed","job_id":42,"kind":"audio","status":"completed","error_code":null,"finished_at":"2026-11-20T12:00:00Z"}';
const SIGNATURE = "v1,v4x2Oq1f9oxWl6yOSIbDL0jilpaF6TfpU2DUHh2/Nzs=";
const PLAIN_SIGNATURE = "v1,oSzWkv28eceA9Z+5CTUK/fZ3SO4QbpdJVBt7E2V2S20=";

const headers = (signature: string, id = MSG_ID, timestamp = TIMESTAMP) =>
  new Headers({ "webhook-id": id, "webhook-timestamp": String(timestamp), "webhook-signature": signature });

describe("verifyWebhook", () => {
  it("accepts the Python signer's signature and returns the event", async () => {
    const event = await verifyWebhook(headers(SIGNATURE), BODY, SECRET, { now: TIMESTAMP + 10 });
    expect(event).toEqual({
      type: "job.completed",
      job_id: 42,
      kind: "audio",
      status: "completed",
      error_code: null,
      finished_at: "2026-11-20T12:00:00Z",
    });
  });

  it("accepts raw bytes, a plain header map and several signatures", async () => {
    const node = {
      "Webhook-Id": MSG_ID,
      "webhook-timestamp": [String(TIMESTAMP)],
      "webhook-signature": `v1,AAAA ${SIGNATURE}`,
    };
    const bytes = new TextEncoder().encode(BODY);
    await expect(verifyWebhook(node, bytes, SECRET, { now: TIMESTAMP })).resolves.toMatchObject({ job_id: 42 });
    await expect(verifyWebhook(headers(SIGNATURE), bytes.buffer, SECRET, { now: TIMESTAMP })).resolves.toMatchObject({
      job_id: 42,
    });
  });

  it("uses a secret without the whsec_ prefix as UTF-8", async () => {
    await expect(
      verifyWebhook(headers(PLAIN_SIGNATURE, "msg_1"), BODY, "plain-secret", { now: TIMESTAMP }),
    ).resolves.toMatchObject({ job_id: 42 });
  });

  it("rejects a changed body, id, secret or signature", async () => {
    const now = { now: TIMESTAMP };
    // Thunks: each promise is created only when its rejection is awaited.
    const cases = [
      () => verifyWebhook(headers(SIGNATURE), BODY.replace("42", "43"), SECRET, now),
      () => verifyWebhook(headers(SIGNATURE, "msg_other"), BODY, SECRET, now),
      () => verifyWebhook(headers(SIGNATURE), BODY, "whsec_AAAA", now),
      () => verifyWebhook(headers(SIGNATURE.replace("v1,", "v2,")), BODY, SECRET, now),
    ];
    for (const run of cases) await expect(run()).rejects.toBeInstanceOf(WebhookVerificationError);
  });

  it("rejects a timestamp outside the tolerance and missing headers", async () => {
    await expect(verifyWebhook(headers(SIGNATURE), BODY, SECRET, { now: TIMESTAMP + 301 })).rejects.toThrow(
      "tolerance",
    );
    await expect(verifyWebhook(headers(SIGNATURE), BODY, SECRET, { now: TIMESTAMP - 301 })).rejects.toThrow(
      "tolerance",
    );
    await expect(verifyWebhook(new Headers(), BODY, SECRET)).rejects.toThrow("missing");
  });
});

describe("secretKey", () => {
  it("decodes whsec_ secrets and refuses invalid base64", () => {
    expect(Array.from(secretKey(SECRET))).toEqual(Array.from({ length: 32 }, (_, i) => i));
    expect(() => secretKey("whsec_not base64!")).toThrow("not valid base64");
  });
});
