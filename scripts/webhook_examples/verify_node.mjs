import { createHmac, timingSafeEqual } from "node:crypto";

// headers: lowercase names (as in Node's req.headers); body: the raw request Buffer.
// Returns the job event, or null when the request is not a fresh, signed hub webhook.
export function verifyWebhook(headers, body, secret, toleranceSeconds = 300) {
  const id = headers["webhook-id"];
  const ts = headers["webhook-timestamp"];
  if (!id || !/^\d+$/.test(ts ?? "")) return null;
  if (Math.abs(Date.now() / 1000 - Number(ts)) > toleranceSeconds) return null;
  const key = secret.startsWith("whsec_") ? Buffer.from(secret.slice(6), "base64") : Buffer.from(secret);
  const mac = createHmac("sha256", key).update(`${id}.${ts}.`).update(body).digest("base64");
  const expected = Buffer.from(`v1,${mac}`);
  const ok = (headers["webhook-signature"] ?? "").split(" ").some((sig) => {
    const given = Buffer.from(sig);
    return given.length === expected.length && timingSafeEqual(given, expected);
  });
  return ok ? JSON.parse(body.toString("utf8")) : null;
}
