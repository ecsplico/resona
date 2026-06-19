import "fake-indexeddb/auto";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useOfflineQueue } from "~/composables/useOfflineQueue";

function makeItem(title: string): {
	blob: Blob;
	title: string;
	language: string;
	profile: string;
	transcribe: boolean;
	duration_seconds: number;
} {
	return {
		blob: new Blob(["audio"], { type: "audio/webm" }),
		title,
		language: "de",
		profile: "default",
		transcribe: true,
		duration_seconds: 3,
	};
}

describe("useOfflineQueue", () => {
	beforeEach(async () => {
		await useOfflineQueue().clearAll();
	});

	it("enqueue stores a pending item returned by pending()", async () => {
		const q = useOfflineQueue();
		const id = await q.enqueue(makeItem("Visite"));
		expect(typeof id).toBe("string");

		const items = await q.pending();
		expect(items.length).toBe(1);
		expect(items[0]!.id).toBe(id);
		expect(items[0]!.title).toBe("Visite");
		// Blob round-trips through structured clone (its type/contents survive).
		expect(items[0]!.blob.type).toBe("audio/webm");
		expect(await items[0]!.blob.text()).toBe("audio");
		expect(typeof items[0]!.createdAt).toBe("number");
	});

	it("sorts pending items by createdAt", async () => {
		const q = useOfflineQueue();
		const first = await q.enqueue(makeItem("A"));
		const second = await q.enqueue(makeItem("B"));
		const items = await q.pending();
		expect(items.map(i => i.id)).toEqual([first, second]);
	});

	it("sync uploads each item and removes succeeded ones (drain)", async () => {
		const q = useOfflineQueue();
		await q.enqueue(makeItem("A"));
		await q.enqueue(makeItem("B"));

		const uploadFn = vi.fn(async () => ({ id: "rec" }));
		const result = await q.sync(uploadFn);

		expect(uploadFn).toHaveBeenCalledTimes(2);
		expect(result.succeeded.length).toBe(2);
		expect(result.failed.length).toBe(0);
		expect(await q.pending()).toEqual([]);
	});

	it("sync keeps items that fail and drains the rest", async () => {
		const q = useOfflineQueue();
		const keep = await q.enqueue(makeItem("Bad"));
		await q.enqueue(makeItem("Good"));

		const uploadFn = vi.fn(async (item: { id: string }) => {
			if (item.id === keep) {
				throw new Error("offline");
			}
			return { id: "rec" };
		});

		const result = await q.sync(uploadFn);

		expect(result.succeeded.length).toBe(1);
		expect(result.failed).toEqual([keep]);

		const remaining = await q.pending();
		expect(remaining.length).toBe(1);
		expect(remaining[0]!.id).toBe(keep);
	});

	it("remove deletes a queued item", async () => {
		const q = useOfflineQueue();
		const id = await q.enqueue(makeItem("X"));
		await q.remove(id);
		expect(await q.pending()).toEqual([]);
	});
});
