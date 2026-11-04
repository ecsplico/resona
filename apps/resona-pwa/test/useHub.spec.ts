import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { HubError, useHub } from "~/composables/useHub";
import { useHubSettings } from "~/composables/useHubSettings";

// Base URL is the real nuxt.config resonaApiUrl ("http://localhost:7710").
const fetchMock = vi.fn();
const realFetch = globalThis.fetch;

function jsonResponse(body: unknown, status = 200, statusText = ""): Response {
	return new Response(JSON.stringify(body), { status, statusText, headers: { "Content-Type": "application/json" } });
}

async function caught(p: Promise<unknown>): Promise<HubError> {
	try {
		await p;
	}
	catch (err) {
		expect(err).toBeInstanceOf(HubError);
		return err as HubError;
	}
	throw new Error("expected a HubError");
}

function lastInit(): RequestInit {
	return fetchMock.mock.calls.at(-1)![1] as RequestInit;
}

describe("useHub", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		globalThis.fetch = fetchMock as unknown as typeof fetch;
		useHubSettings().token = "tok";
	});

	afterEach(() => {
		globalThis.fetch = realFetch;
		useHubSettings().token = "";
	});

	it("sends the device token as a Bearer header", async () => {
		fetchMock.mockResolvedValue(jsonResponse({ engines: [], default: null }));
		const hub = useHub();
		expect(hub.hasToken()).toBe(true);
		expect(hub.headers()).toEqual({ Authorization: "Bearer tok" });
		await hub.listEngines();
		expect(fetchMock).toHaveBeenCalledWith("http://localhost:7710/v1/engines", expect.anything());
		expect(lastInit().headers).toEqual(expect.objectContaining({ Authorization: "Bearer tok" }));
	});

	it("sends no Authorization header without a token", () => {
		useHubSettings().token = "";
		const hub = useHub();
		expect(hub.hasToken()).toBe(false);
		expect(hub.headers()).toEqual({});
	});

	it("maps the error body to code and message", async () => {
		fetchMock.mockResolvedValue(jsonResponse({ error: "unauthorized", message: "invalid token" }, 401, "Unauthorized"));
		const err = await caught(useHub().listEngines());
		expect(err.status).toBe(401);
		expect(err.code).toBe("unauthorized");
		expect(err.message).toBe("invalid token");
	});

	it("falls back to http_<status> and the status text without a JSON body", async () => {
		fetchMock.mockResolvedValue(new Response("boom", { status: 500, statusText: "Internal Server Error" }));
		const err = await caught(useHub().listEngines());
		expect(err.status).toBe(500);
		expect(err.code).toBe("http_500");
		expect(err.message).toBe("Internal Server Error");
	});

	it("reports a network failure as status 0", async () => {
		fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
		const err = await caught(useHub().listEngines());
		expect(err.status).toBe(0);
	});

	it("transcribeSync posts multipart with an Idempotency-Key and omits empty engine/profile", async () => {
		fetchMock.mockResolvedValue(jsonResponse({ text: "hallo" }));
		const text = await useHub().transcribeSync(new Blob(["x"]), "dictation.webm", { language: "de", engine: "", profile: "" });
		expect(text).toBe("hallo");
		expect(fetchMock).toHaveBeenCalledWith("http://localhost:7710/v1/audio/transcriptions", expect.anything());
		const init = lastInit();
		expect(init.method).toBe("POST");
		const headers = init.headers as Record<string, string>;
		expect(headers.Authorization).toBe("Bearer tok");
		expect(headers["Idempotency-Key"]).toMatch(/.+/);
		const form = init.body as FormData;
		expect(form.get("file")).toBeTruthy();
		expect(form.get("language")).toBe("de");
		expect(form.get("response_format")).toBe("json");
		expect(form.has("engine")).toBe(false);
		expect(form.has("profile")).toBe(false);
	});

	it("transcribeSync sends engine/profile when given", async () => {
		fetchMock.mockResolvedValue(jsonResponse({ text: "" }));
		await useHub().transcribeSync(new Blob(["x"]), "d.webm", { language: "de", engine: "whisper", profile: "medical" });
		const form = lastInit().body as FormData;
		expect(form.get("engine")).toBe("whisper");
		expect(form.get("profile")).toBe("medical");
	});

	it("transcribeSync maps a 504 to code timeout", async () => {
		fetchMock.mockResolvedValue(jsonResponse({ error: "gateway_timeout", job_id: 7 }, 504, "Gateway Timeout"));
		const err = await caught(useHub().transcribeSync(new Blob(["x"]), "d.webm", { language: "de" }));
		expect(err.status).toBe(504);
		expect(err.code).toBe("timeout");
	});

	it("listListenEngines returns an empty list on 404", async () => {
		fetchMock.mockResolvedValue(jsonResponse({ detail: "Not Found" }, 404, "Not Found"));
		expect(await useHub().listListenEngines()).toEqual({ engines: [], default: null });
	});

	it("listListenEngines rethrows other errors", async () => {
		fetchMock.mockResolvedValue(jsonResponse({ error: "unauthorized", message: "nope" }, 401));
		const err = await caught(useHub().listListenEngines());
		expect(err.status).toBe(401);
	});

	it("listProfiles maps profiles[].name", async () => {
		fetchMock.mockResolvedValue(jsonResponse({ profiles: [{ name: "default", revision: 1 }] }));
		expect(await useHub().listProfiles()).toEqual(["default"]);
		expect(fetchMock).toHaveBeenCalledWith("http://localhost:7710/profiles", expect.anything());
	});

	it("createJob always sends private: true with the Idempotency-Key", async () => {
		fetchMock.mockResolvedValue(jsonResponse({ job_id: 42, status: "pending" }, 202));
		const id = await useHub().createJob({ kind: "audio", upload_id: "u1", language: "de" }, "key-1");
		expect(id).toBe(42);
		const init = lastInit();
		expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBe("key-1");
		expect(JSON.parse(init.body as string)).toEqual({ kind: "audio", upload_id: "u1", language: "de", private: true });
	});
});
