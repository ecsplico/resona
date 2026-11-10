import { describe, it, expect, vi, beforeEach, afterEach, type MockInstance } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { HubError } from "~/composables/useHub";
import { TITLE_INPUT_MAX_CHARS, titleFromResult, truncateForTitle, useTitleJob } from "~/composables/useTitleJob";

const hub = vi.hoisted(() => ({
	hasToken: vi.fn(),
	upload: vi.fn(),
	createJob: vi.fn(),
	getJob: vi.fn(),
	getResult: vi.fn(),
	deleteJob: vi.fn(),
}));

mockNuxtImport("useHub", () => () => hub);

const TEXT = "Kontrolle des rechten Knies bei Frau Beispiel, keine Schwellung.";

function pending() {
	return { status: "pending", error: null, candidate: null, queue_reason: "no_capable_worker" };
}

describe("useTitleJob", () => {
	let info: MockInstance<typeof console.info>;

	beforeEach(() => {
		vi.clearAllMocks();
		hub.hasToken.mockReturnValue(true);
		hub.upload.mockResolvedValue("upl-1");
		hub.createJob.mockResolvedValue(7);
		hub.getJob.mockResolvedValue({ status: "completed", error: null, candidate: null, queue_reason: null });
		hub.getResult.mockResolvedValue({ structured: { title: { title: "Befund Kontrolluntersuchung Knie" } } });
		hub.deleteJob.mockResolvedValue(undefined);
		info = vi.spyOn(console, "info").mockImplementation(() => {});
	});

	afterEach(() => {
		info.mockRestore();
	});

	function logged(): string {
		return info.mock.calls.map(call => call.join(" ")).join("\n");
	}

	it("returns the title of a text job and deletes the job", async () => {
		const title = await useTitleJob().suggestTitleRemote(TEXT, { language: "de" });

		expect(title).toBe("Befund Kontrolluntersuchung Knie");
		const [file, filename] = hub.upload.mock.calls[0]!;
		expect(filename).toMatch(/\.md$/);
		expect(await (file as Blob).text()).toBe(TEXT);
		const [body, key] = hub.createJob.mock.calls[0]!;
		expect(body).toEqual({ kind: "text", upload_id: "upl-1", profile: "title-de", language: "de" });
		expect(key).toMatch(/^title:/);
		expect(hub.deleteJob).toHaveBeenCalledWith(7);
		expect(logged()).toContain("ok");
		expect(logged()).not.toContain("Knie");
	});

	it("polls until the job completes", async () => {
		hub.getJob.mockResolvedValueOnce(pending()).mockResolvedValueOnce({ status: "completed", error: null });

		expect(await useTitleJob().suggestTitleRemote(TEXT)).toBe("Befund Kontrolluntersuchung Knie");
		expect(hub.getJob).toHaveBeenCalledTimes(2);
	});

	it("returns null without a token and calls nothing", async () => {
		hub.hasToken.mockReturnValue(false);

		expect(await useTitleJob().suggestTitleRemote(TEXT)).toBeNull();
		expect(hub.upload).not.toHaveBeenCalled();
		expect(logged()).toContain("no_token");
	});

	it("returns null when the profile is missing on the hub", async () => {
		hub.createJob.mockRejectedValue(new HubError(404, "profile_not_found", "nope"));

		expect(await useTitleJob().suggestTitleRemote(TEXT)).toBeNull();
		expect(hub.deleteJob).not.toHaveBeenCalled();
		expect(logged()).toContain("hub:profile_not_found");
	});

	it("returns null for a failed job and deletes it", async () => {
		hub.getJob.mockResolvedValue({ status: "failed", error: { code: "policy_denied", message: "x" } });

		expect(await useTitleJob().suggestTitleRemote(TEXT)).toBeNull();
		expect(hub.deleteJob).toHaveBeenCalledWith(7);
		expect(logged()).toContain("job_failed:policy_denied");
	});

	it("returns null on timeout and deletes the waiting job", async () => {
		hub.getJob.mockResolvedValue(pending());

		expect(await useTitleJob().suggestTitleRemote(TEXT, { timeoutMs: 50 })).toBeNull();
		expect(hub.deleteJob).toHaveBeenCalledWith(7);
		expect(logged()).toContain("timeout");
	});

	it("returns null on abort and deletes the job", async () => {
		hub.getJob.mockResolvedValue(pending());
		const controller = new AbortController();
		setTimeout(() => controller.abort(), 20);

		expect(await useTitleJob().suggestTitleRemote(TEXT, { signal: controller.signal })).toBeNull();
		expect(hub.deleteJob).toHaveBeenCalledWith(7);
		expect(logged()).toContain("aborted");
	});

	it("deletes a job whose creation answers after the timeout", async () => {
		let created!: (id: number) => void;
		hub.createJob.mockReturnValue(new Promise<number>((resolve) => {
			created = resolve;
		}));

		expect(await useTitleJob().suggestTitleRemote(TEXT, { timeoutMs: 20 })).toBeNull();
		expect(hub.deleteJob).not.toHaveBeenCalled();
		created(9);
		await vi.waitFor(() => expect(hub.deleteJob).toHaveBeenCalledWith(9));
	});

	it("returns null when the result has no usable title", async () => {
		hub.getResult.mockResolvedValue({ structured: { title: { _raw: "kaputt" } } });

		expect(await useTitleJob().suggestTitleRemote(TEXT)).toBeNull();
		expect(hub.deleteJob).toHaveBeenCalledWith(7);
		expect(logged()).toContain("no_title");
	});

	it("uploads at most the first 4000 characters", async () => {
		await useTitleJob().suggestTitleRemote("Wort ".repeat(2000));

		const sent = await (hub.upload.mock.calls[0]![0] as Blob).text();
		expect(sent.length).toBeLessThanOrEqual(TITLE_INPUT_MAX_CHARS);
		expect(sent.endsWith("Wort")).toBe(true);
	});
});

describe("truncateForTitle", () => {
	it("keeps short text and cuts long text at a word boundary", () => {
		expect(truncateForTitle("  kurz  ")).toBe("kurz");
		const long = `${"a".repeat(3990)} bcdefghijklmnop`;
		expect(truncateForTitle(long)).toBe("a".repeat(3990));
	});

	it("cuts hard when there is no space near the end", () => {
		expect(truncateForTitle("x".repeat(5000))).toHaveLength(TITLE_INPUT_MAX_CHARS);
	});
});

describe("titleFromResult", () => {
	it("reads structured.title.title or a bare string, cleaned", () => {
		expect(titleFromResult({ structured: { title: { title: " „Befund Knie“. " } } })).toBe("Befund Knie");
		expect(titleFromResult({ structured: { title: "Arztbrief Entlassung" } })).toBe("Arztbrief Entlassung");
	});

	it("rejects missing, empty and overlong titles", () => {
		expect(titleFromResult({ structured: null })).toBeNull();
		expect(titleFromResult({ structured: { title: { title: "  " } } })).toBeNull();
		expect(titleFromResult({ structured: { title: { title: "x".repeat(81) } } })).toBeNull();
	});
});
