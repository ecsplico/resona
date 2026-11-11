import "fake-indexeddb/auto";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
	appendUnfinishedChunk,
	beginUnfinished,
	claimUnfinished,
	discardUnfinished,
	listUnfinished,
	loadUnfinished,
	releaseUnfinished,
	type UnfinishedRecording,
} from "~/data/unfinishedRecordings";

const merge = vi.hoisted(() => ({
	mergeToWav: vi.fn(async (_parts: Blob[]) => ({ blob: new Blob(["merged"], { type: "audio/wav" }), seconds: 12 })),
}));
vi.mock("~/utils/audioMerge", () => merge);

function meta(id: string, startedAt: string): UnfinishedRecording {
	return { id, kind: "record", label: null, mime: "audio/webm", startedAt, updatedAt: startedAt, elapsed: 0 };
}

const chunk = (text: string) => new Blob([text], { type: "audio/webm" });

describe("unfinishedRecordings", () => {
	beforeEach(async () => {
		vi.clearAllMocks();
		for (const m of await listUnfinished()) await discardUnfinished(m.id);
	});

	it("offers what a page left behind, oldest first, with its recorded audio", async () => {
		await beginUnfinished(meta("b", "2026-11-11T10:00:00Z"));
		await beginUnfinished(meta("a", "2026-11-11T09:00:00Z"));
		await appendUnfinishedChunk("b", 0, 0, chunk("one-"), 4);
		await appendUnfinishedChunk("b", 0, 1, chunk("two"), 8);

		expect((await listUnfinished()).map(m => m.id)).toEqual(["a", "b"]);
		const recovered = await loadUnfinished("b");
		expect(await recovered!.blob.text()).toBe("one-two");
		expect(recovered!.seconds).toBe(8);
		expect(recovered!.meta.elapsed).toBe(8);
		expect(merge.mergeToWav).not.toHaveBeenCalled();
	});

	it("a chunk landing after discard does not bring the session back", async () => {
		await beginUnfinished(meta("late", "2026-11-11T10:00:00Z"));
		await discardUnfinished("late");
		await appendUnfinishedChunk("late", 0, 0, chunk("x"), 2);
		expect(await listUnfinished()).toEqual([]);
	});

	it("never offers a session this page still records or holds", async () => {
		await beginUnfinished(meta("live", "2026-11-11T10:00:00Z"));
		claimUnfinished("live");
		expect(await listUnfinished()).toEqual([]);
		releaseUnfinished("live");
		expect((await listUnfinished()).map(m => m.id)).toEqual(["live"]);
	});

	it("joins resumed parts into one recording, in part order", async () => {
		await beginUnfinished(meta("p", "2026-11-11T10:00:00Z"));
		await appendUnfinishedChunk("p", 1, 0, chunk("second"), 9);
		await appendUnfinishedChunk("p", 0, 0, chunk("first"), 4);

		const recovered = await loadUnfinished("p");
		const parts = merge.mergeToWav.mock.calls[0]![0];
		expect(await Promise.all(parts.map(p => p.text()))).toEqual(["first", "second"]);
		expect(recovered!.blob.type).toBe("audio/wav");
		expect(recovered!.seconds).toBe(12);
	});

	it("a session without audio loads as null; discard removes session and audio", async () => {
		await beginUnfinished(meta("empty", "2026-11-11T10:00:00Z"));
		expect(await loadUnfinished("empty")).toBeNull();

		await beginUnfinished(meta("gone", "2026-11-11T11:00:00Z"));
		await appendUnfinishedChunk("gone", 0, 0, chunk("x"), 1);
		// A session whose id is a prefix of another keeps its own chunks.
		await beginUnfinished(meta("gone-2", "2026-11-11T12:00:00Z"));
		await appendUnfinishedChunk("gone-2", 0, 0, chunk("y"), 1);
		await discardUnfinished("gone");

		expect(await loadUnfinished("gone")).toBeNull();
		expect(await (await loadUnfinished("gone-2"))!.blob.text()).toBe("y");
		expect((await listUnfinished()).map(m => m.id)).toEqual(["empty", "gone-2"]);
	});
});
