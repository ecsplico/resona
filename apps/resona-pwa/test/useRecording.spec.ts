import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { effectScope } from "vue";
import { useRecording } from "~/composables/useRecording";
import type { Recording, Transcript } from "~/types/directus";

const state = vi.hoisted(() => ({
	request: vi.fn(),
	readItem: vi.fn((collection: string, id: string) => ({ kind: "readItem", collection, id })),
	readItems: vi.fn((collection: string, query: unknown) => ({ kind: "readItems", collection, query })),
}));

mockNuxtImport("useDirectus", () => () => ({ client: { request: state.request } }));

vi.mock("@directus/sdk", () => ({
	readItem: (collection: string, id: string) => state.readItem(collection, id),
	readItems: (collection: string, query: unknown) => state.readItems(collection, query),
}));

function rec(id: string, status: Recording["status"]): Recording {
	return {
		id,
		title: `rec-${id}`,
		language: "de",
		profile: "default",
		status,
		source: "batch",
	};
}

function tx(id: string, recording: string): Transcript {
	return { id, recording, text: `text-${id}` };
}

// request() is called as request(readItem(...)) then request(readItems(...)).
// Distinguish by the descriptor's kind.
function wireFetch(recording: Recording | null, transcripts: Transcript[]): void {
	state.request.mockImplementation((descriptor: { kind: string }) => {
		if (descriptor.kind === "readItem") {
			return Promise.resolve(recording);
		}
		return Promise.resolve(transcripts);
	});
}

describe("useRecording", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		vi.useFakeTimers();
		state.readItem.mockImplementation((collection: string, id: string) => ({ kind: "readItem", collection, id }));
		state.readItems.mockImplementation((collection: string, query: unknown) => ({ kind: "readItems", collection, query }));
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it("refresh() fetches recording + transcript, populating both refs", async () => {
		wireFetch(rec("1", "done"), [tx("t1", "1")]);
		const r = useRecording("1");

		await r.refresh();

		expect(state.readItem).toHaveBeenCalledWith("recordings", "1");
		const [, query] = state.readItems.mock.calls[0];
		expect(query).toEqual({ filter: { recording: { _eq: "1" } }, limit: 1 });
		expect(r.recording.value).toEqual(rec("1", "done"));
		expect(r.transcript.value).toEqual(tx("t1", "1"));
		expect(r.loading.value).toBe(false);
		expect(r.error.value).toBeNull();
		r.stopPolling();
	});

	it("transcript is null when none returned", async () => {
		wireFetch(rec("1", "done"), []);
		const r = useRecording("1");

		await r.refresh();

		expect(r.transcript.value).toBeNull();
		r.stopPolling();
	});

	it("captures error and ends loading false on rejection", async () => {
		state.request.mockRejectedValue(new Error("boom"));
		const r = useRecording("1");

		await r.refresh();

		expect(r.error.value).toBeTypeOf("string");
		expect(r.error.value).not.toBeNull();
		expect(r.loading.value).toBe(false);
		r.stopPolling();
	});

	it("polls until status becomes done, then stops the interval", async () => {
		// pending for first two fetches, then done.
		let calls = 0;
		state.request.mockImplementation((descriptor: { kind: string }) => {
			if (descriptor.kind === "readItem") {
				calls += 1;
				return Promise.resolve(rec("1", calls >= 3 ? "done" : "pending"));
			}
			return Promise.resolve([tx("t1", "1")]);
		});

		const r = useRecording("1");
		r.startPolling(1000);

		// initial refresh (1 readItem), still pending
		await vi.advanceTimersByTimeAsync(0);
		expect(state.readItem).toHaveBeenCalledTimes(1);
		expect(r.recording.value?.status).toBe("pending");

		// tick 1 -> 2nd fetch, still pending
		await vi.advanceTimersByTimeAsync(1000);
		expect(state.readItem).toHaveBeenCalledTimes(2);
		expect(r.recording.value?.status).toBe("pending");

		// tick 2 -> 3rd fetch, becomes done -> interval cleared
		await vi.advanceTimersByTimeAsync(1000);
		expect(state.readItem).toHaveBeenCalledTimes(3);
		expect(r.recording.value?.status).toBe("done");

		// further time must not trigger more fetches
		await vi.advanceTimersByTimeAsync(5000);
		expect(state.readItem).toHaveBeenCalledTimes(3);
		r.stopPolling();
	});

	it("does not keep an interval when already terminal on first refresh", async () => {
		wireFetch(rec("1", "done"), [tx("t1", "1")]);
		const r = useRecording("1");

		r.startPolling(1000);
		await vi.advanceTimersByTimeAsync(0);
		expect(state.readItem).toHaveBeenCalledTimes(1);

		await vi.advanceTimersByTimeAsync(10000);
		expect(state.readItem).toHaveBeenCalledTimes(1);
		r.stopPolling();
	});

	it("guards against multiple concurrent intervals", async () => {
		wireFetch(rec("1", "pending"), []);
		const r = useRecording("1");

		r.startPolling(1000);
		r.startPolling(1000);
		await vi.advanceTimersByTimeAsync(0);
		// two initial refreshes would be 2; guarded second start must be a no-op
		expect(state.readItem).toHaveBeenCalledTimes(1);

		await vi.advanceTimersByTimeAsync(1000);
		// single interval -> one more fetch
		expect(state.readItem).toHaveBeenCalledTimes(2);
		r.stopPolling();
	});

	it("stopPolling clears the interval", async () => {
		wireFetch(rec("1", "pending"), []);
		const r = useRecording("1");

		r.startPolling(1000);
		await vi.advanceTimersByTimeAsync(0);
		expect(state.readItem).toHaveBeenCalledTimes(1);

		r.stopPolling();
		await vi.advanceTimersByTimeAsync(10000);
		expect(state.readItem).toHaveBeenCalledTimes(1);
	});

	it("onScopeDispose stops polling when the scope is disposed", async () => {
		wireFetch(rec("1", "pending"), []);
		const scope = effectScope();
		let r!: ReturnType<typeof useRecording>;
		scope.run(() => {
			r = useRecording("1");
			r.startPolling(1000);
		});

		await vi.advanceTimersByTimeAsync(0);
		expect(state.readItem).toHaveBeenCalledTimes(1);

		scope.stop();
		await vi.advanceTimersByTimeAsync(10000);
		expect(state.readItem).toHaveBeenCalledTimes(1);
	});
});
