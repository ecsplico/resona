import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { CONTEXT_MAX_CHARS, liveCleanupContext, useLiveCleanup } from "~/composables/useLiveCleanup";
import { HubError } from "~/composables/useHub";
import type { InsertionTarget } from "~/utils/insertionGuard";

const hub = vi.hoisted(() => ({
	hasToken: vi.fn(() => true),
	upload: vi.fn(),
	createJob: vi.fn(),
	getJob: vi.fn(),
	getResult: vi.fn(),
	deleteJob: vi.fn(),
}));
mockNuxtImport("useHub", () => () => hub);

const OPTS = { profile: "dictation-clean-de", language: "de", context: { before: "# Visite" } };

function target(text: string, owner = "live-1"): InsertionTarget {
	return { ownerKey: owner, text, selection: { start: 0, end: text.length } };
}

describe("useLiveCleanup", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		hub.hasToken.mockReturnValue(true);
		hub.upload.mockResolvedValue("up-1");
		hub.createJob.mockResolvedValue(7);
		hub.getJob.mockResolvedValue({ id: 7, status: "completed", error: null });
		hub.getResult.mockResolvedValue({ markdown: "Der Befund ist unauffällig.\n" });
		hub.deleteJob.mockResolvedValue(undefined);
	});

	it("sends a text job with the profile and context, then deletes it", async () => {
		const cleaned = await useLiveCleanup().cleanText("äh der Befund", OPTS);
		expect(cleaned).toBe("Der Befund ist unauffällig.");
		expect(hub.createJob).toHaveBeenCalledWith(
			{ kind: "text", upload_id: "up-1", profile: "dictation-clean-de", context: { before: "# Visite" }, language: "de" },
			expect.stringMatching(/^live-cleanup:/),
		);
		expect(hub.deleteJob).toHaveBeenCalledWith(7);
	});

	it("lands the cleaned text through the insertion guard while the text is unchanged", async () => {
		const result = await useLiveCleanup().cleanupInto(target("äh der Befund"), () => target("äh der Befund"), OPTS);
		expect(result).toMatchObject({ kind: "insert", text: "Der Befund ist unauffällig." });
	});

	it("drops the result when the text changed meanwhile", async () => {
		const result = await useLiveCleanup().cleanupInto(target("äh der Befund"), () => target("äh der Befund, getippt"), OPTS);
		expect(result).toMatchObject({ kind: "dropped" });
	});

	it("drops the result when another session owns the text now", async () => {
		const result = await useLiveCleanup().cleanupInto(target("äh der Befund"), () => target("äh der Befund", "live-2"), OPTS);
		expect(result).toEqual({ kind: "dropped", reason: "owner-changed" });
	});

	it("returns null without a token and calls nothing", async () => {
		hub.hasToken.mockReturnValue(false);
		expect(await useLiveCleanup().cleanText("text", OPTS)).toBeNull();
		expect(hub.upload).not.toHaveBeenCalled();
	});

	it("returns null on a failed job or a hub error", async () => {
		hub.getJob.mockResolvedValueOnce({ id: 7, status: "failed", error: { code: "postprocess_failed", message: "x" } });
		expect(await useLiveCleanup().cleanText("text", OPTS)).toBeNull();
		expect(hub.deleteJob).toHaveBeenCalledWith(7);

		hub.createJob.mockRejectedValueOnce(new HubError(422, "invalid_context", "nope"));
		expect(await useLiveCleanup().cleanText("text", OPTS)).toBeNull();
	});

	it("leaves out an absent context", async () => {
		await useLiveCleanup().cleanText("text", { profile: "p", context: null });
		expect(hub.createJob.mock.calls[0]![0]).not.toHaveProperty("context");
	});
});

describe("liveCleanupContext", () => {
	it("puts the title as the heading before the text, capped at the hub limit", () => {
		expect(liveCleanupContext("  Visite  ")).toEqual({ before: "# Visite" });
		expect(liveCleanupContext("x".repeat(5000))!.before).toHaveLength(CONTEXT_MAX_CHARS);
	});

	it("is null without a title", () => {
		expect(liveCleanupContext("   ")).toBeNull();
	});
});
