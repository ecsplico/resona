import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useServerHealth } from "~/composables/useServerHealth";

const fetchMock = vi.fn();
const realFetch = globalThis.fetch;

describe("useServerHealth", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		globalThis.fetch = fetchMock as unknown as typeof fetch;
	});

	afterEach(() => {
		globalThis.fetch = realFetch;
		const health = useServerHealth();
		health.stop();
	});

	it("reports 'up' when both backends answer", async () => {
		fetchMock.mockResolvedValue(new Response(null, { status: 200 }));
		const health = useServerHealth();
		await health.check();
		expect(health.directus.value).toBe(true);
		expect(health.resonaApi.value).toBe(true);
		expect(health.overall.value).toBe("up");
	});

	it("reports 'partial' when one backend is unreachable", async () => {
		fetchMock
			.mockResolvedValueOnce(new Response(null, { status: 200 })) // directus
			.mockRejectedValueOnce(new Error("refused")); // resona-api
		const health = useServerHealth();
		await health.check();
		expect(health.directus.value).toBe(true);
		expect(health.resonaApi.value).toBe(false);
		expect(health.overall.value).toBe("partial");
	});

	it("reports 'down' when both backends fail", async () => {
		fetchMock.mockRejectedValue(new Error("network"));
		const health = useServerHealth();
		await health.check();
		expect(health.overall.value).toBe("down");
	});

	it("pings the configured health endpoints with no-cors", async () => {
		fetchMock.mockResolvedValue(new Response(null, { status: 200 }));
		const health = useServerHealth();
		await health.check();
		const urls = fetchMock.mock.calls.map(c => String(c[0]));
		expect(urls.some(u => u.endsWith("/server/health"))).toBe(true);
		expect(urls.some(u => u.endsWith("/health"))).toBe(true);
		expect(fetchMock.mock.calls[0]![1]).toMatchObject({ mode: "no-cors" });
	});
});
