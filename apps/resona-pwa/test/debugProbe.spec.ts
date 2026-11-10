import { describe, it, expect, vi, afterEach } from "vitest";
import { request as httpRequest, createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { createApp, toNodeListener, toWebHandler } from "h3";
import { buildDebugPayload, guessDeviceOs, runDiagnose } from "~/utils/debugProbe";
import type { DebugReport } from "~/utils/debugProbe";
import { DEBUG_REPORT_MAX_BYTES } from "#shared/debugReport";
import debugReportRoute from "~~/server/api/debug-report.post";

const TOKEN = "dev-token-9f3a7c21e8";
const DICTATED = "Patient klagt über Knieschmerzen rechts";

function report(overrides: Partial<DebugReport> = {}): DebugReport {
	return {
		version: 1,
		createdAt: "2026-11-10T08:00:00.000Z",
		durationMs: 1234,
		context: { hubUrl: "https://hub.example", token: TOKEN },
		checks: [
			{
				id: "listen-socket",
				group: "Hub",
				label: "WebSocket /v1/listen",
				state: "fail",
				detail: `Konstruktor: SyntaxError: The URL 'wss://hub.example/v1/listen?language=de&token=${TOKEN}' is invalid`,
				data: { url: `wss://hub.example/v1/listen?token=${TOKEN}&language=de`, transcript: DICTATED, headers: { authorization: `Bearer ${TOKEN}` } },
			},
		],
		pttLog: [{ ts: 1, stage: "upload", ok: false, detail: `HTTP 401 for Bearer ${TOKEN}` }],
		...overrides,
	};
}

describe("buildDebugPayload", () => {
	it("never contains the device token or dictated text", () => {
		const body = buildDebugPayload(report(), [TOKEN]);
		expect(body).not.toContain(TOKEN);
		expect(body).not.toContain(DICTATED);
		expect(body).toContain("[redacted]");
		// Non-secret diagnostics survive.
		const parsed = JSON.parse(body) as DebugReport;
		expect(parsed.checks[0]!.detail).toContain("is invalid");
		expect(parsed.checks[0]!.data!.url).toContain("language=de");
	});

	it("shrinks an oversized report under the route limit, still redacted", () => {
		const big = report();
		big.checks = Array.from({ length: 60 }, (_, i) => ({
			id: `c${i}`,
			group: "Audio",
			label: "x",
			state: "info" as const,
			detail: `${TOKEN} ${"y".repeat(400)}`,
			data: { blob: "z".repeat(2000) },
		}));
		const body = buildDebugPayload(big, [TOKEN]);
		expect(new TextEncoder().encode(body).length).toBeLessThanOrEqual(DEBUG_REPORT_MAX_BYTES);
		expect(body).not.toContain(TOKEN);
	});
});

describe("guessDeviceOs", () => {
	it("reads the iOS version from an iPhone UA", () => {
		const ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";
		expect(guessDeviceOs(ua)).toEqual({ os: "iOS", version: "17.5.1", mobile: true });
	});

	it("recognises an iPad posing as desktop Safari by its touch points", () => {
		const ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15";
		expect(guessDeviceOs(ua, 5)).toMatchObject({ os: "iPadOS (Desktop-UA)", version: "18.1", mobile: true });
		expect(guessDeviceOs(ua, 0).os).toBe("macOS");
	});

	it("reads the Android version", () => {
		const ua = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36";
		expect(guessDeviceOs(ua)).toEqual({ os: "Android", version: "14", mobile: true });
	});
});

describe("runDiagnose", () => {
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it("uses the token for the live socket but keeps it and the transcript out of the report", async () => {
		const opened: string[] = [];
		class FakeSocket {
			static OPEN = 1;
			readyState = 0;
			binaryType = "blob";
			onopen: (() => void) | null = null;
			onmessage: ((e: { data: unknown }) => void) | null = null;
			onerror: (() => void) | null = null;
			onclose: ((e: { code: number; reason: string; wasClean: boolean }) => void) | null = null;
			constructor(url: string) {
				opened.push(url);
				setTimeout(() => {
					this.readyState = 1;
					this.onopen?.();
				}, 0);
			}

			send(data: unknown): void {
				if (typeof data === "string" || this.readyState !== 1) return;
				this.readyState = 3;
				this.onmessage?.({ data: JSON.stringify({ type: "Results", is_final: true, channel: { alternatives: [{ transcript: DICTATED }] } }) });
				this.onclose?.({ code: 1000, reason: "", wasClean: true });
			}

			close(): void {}
		}
		vi.stubGlobal("WebSocket", FakeSocket);
		vi.stubGlobal("fetch", vi.fn(async () => new Response("ok", { status: 200 })));

		const result = await runDiagnose({
			hubUrl: "https://hub.example",
			language: "de",
			token: TOKEN,
			hubFetch: async () => new Response(JSON.stringify({ engines: [{ name: "faster-whisper", kind: "local", private: true, models: [] }], default: "faster-whisper" }), { status: 200 }),
			liveSettings: { source: "hub", hubEngine: "", correction: false, chunkSeconds: 15 },
		});

		expect(opened).toHaveLength(1);
		expect(new URL(opened[0]!).searchParams.get("token")).toBe(TOKEN);
		const socket = result.checks.find(c => c.id === "listen-socket")!;
		expect(socket.state).toBe("ok");
		expect(socket.data!.messages).toEqual([expect.objectContaining({ type: "Results", isFinal: true, transcriptChars: DICTATED.length })]);
		const body = buildDebugPayload(result, [TOKEN]);
		expect(JSON.stringify(result)).not.toContain(TOKEN);
		expect(body).not.toContain(TOKEN);
		expect(body).not.toContain(DICTATED);
		// Missing browser APIs are reported, not thrown.
		expect(result.checks.find(c => c.id === "get-user-media")?.state).toBe("fail");
		expect(result.checks.find(c => c.id === "listen-engines")?.state).toBe("ok");
	});
});

describe("POST /api/debug-report", () => {
	const handler = toWebHandler(createApp().use("/api/debug-report", debugReportRoute));

	// Each test sends from its own client IP so the per-IP rate limit (module
	// state of the route) never leaks between tests.
	function post(body: BodyInit, ip: string): Promise<Response> {
		return handler(new Request("http://localhost/api/debug-report", {
			method: "POST",
			headers: { "Content-Type": "application/json", "X-Forwarded-For": ip },
			body,
			duplex: "half",
		} as RequestInit));
	}

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it("logs one redacted [resona-debug] line and returns an id", async () => {
		const log = vi.spyOn(console, "log").mockImplementation(() => {});
		const resp = await post(JSON.stringify({ checks: [{ detail: `wss://h/v1/listen?token=${TOKEN}` }], token: TOKEN, transcript: DICTATED }), "10.0.0.1");
		expect(resp.status).toBe(200);
		const { id } = await resp.json() as { id: string };
		expect(id).toMatch(/^[\da-f]{8}$/);
		expect(log).toHaveBeenCalledTimes(1);
		const line = String(log.mock.calls[0]![0]);
		expect(line.startsWith("[resona-debug] ")).toBe(true);
		expect(line).toContain(id);
		expect(line).not.toContain(TOKEN);
		expect(line).not.toContain(DICTATED);
	});

	it("rejects a body over 32 KB without logging", async () => {
		const log = vi.spyOn(console, "log").mockImplementation(() => {});
		const resp = await post(JSON.stringify({ pad: "x".repeat(DEBUG_REPORT_MAX_BYTES) }), "10.0.0.2");
		expect(resp.status).toBe(413);
		expect(log).not.toHaveBeenCalled();
	});

	it("rejects a body that is not a JSON object", async () => {
		const log = vi.spyOn(console, "log").mockImplementation(() => {});
		expect((await post("not json", "10.0.0.3")).status).toBe(400);
		expect((await post("[1,2]", "10.0.0.3")).status).toBe(400);
		expect(log).not.toHaveBeenCalled();
	});

	it("stops reading an oversized chunked body without Content-Length at the cap", async () => {
		const log = vi.spyOn(console, "log").mockImplementation(() => {});
		const chunk = new TextEncoder().encode("x".repeat(8 * 1024));
		let pulled = 0;
		let cancelled = false;
		const body = new ReadableStream<Uint8Array>({
			pull(controller) {
				pulled += 1;
				if (pulled > 1000) {
					controller.close();
					return;
				}
				controller.enqueue(chunk);
			},
			cancel() {
				cancelled = true;
			},
		});
		const resp = await post(body, "10.0.0.4");
		expect(resp.status).toBe(413);
		// 32 KB cap / 8 KB chunks: the fifth chunk crosses it (plus a little read-ahead).
		expect(pulled).toBeLessThanOrEqual(7);
		expect(cancelled).toBe(true);
		expect(log).not.toHaveBeenCalled();
	});

	it("answers 429 to the 11th report of one client within the window", async () => {
		vi.spyOn(console, "log").mockImplementation(() => {});
		const statuses: number[] = [];
		for (let i = 0; i < 11; i++) {
			statuses.push((await post("{}", "10.0.0.5")).status);
		}
		expect(statuses.slice(0, 10)).toEqual(Array.from({ length: 10 }, () => 200));
		expect(statuses[10]).toBe(429);
		expect((await post("{}", "10.0.0.6")).status).toBe(200);
	});

	it("caps a chunked body on the Node server path too", async () => {
		const log = vi.spyOn(console, "log").mockImplementation(() => {});
		const server = createServer(toNodeListener(createApp().use("/api/debug-report", debugReportRoute)));
		await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
		try {
			const { port } = server.address() as AddressInfo;
			const status = await new Promise<number>((resolve, reject) => {
				const req = httpRequest({ host: "127.0.0.1", port, path: "/api/debug-report", method: "POST", headers: { "Content-Type": "application/json", "X-Forwarded-For": "10.0.0.7" } }, (res) => {
					res.resume();
					resolve(res.statusCode ?? 0);
				});
				req.on("error", reject);
				// Chunked (no Content-Length), twice the cap.
				const chunk = "x".repeat(8 * 1024);
				for (let i = 0; i < 8; i++) req.write(chunk);
				req.end();
			});
			expect(status).toBe(413);
			expect(log).not.toHaveBeenCalled();
		}
		finally {
			await new Promise(resolve => server.close(resolve));
		}
	});
});
