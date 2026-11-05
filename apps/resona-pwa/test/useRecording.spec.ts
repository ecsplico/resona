import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { effectScope } from "vue";
import { useRecording } from "~/composables/useRecording";
import type { Recording, Transcript } from "~/types/directus";

const state = vi.hoisted(() => ({
	getRecording: vi.fn(),
	getTranscript: vi.fn(),
}));

mockNuxtImport("useDataStore", () => () => ({
	getRecording: state.getRecording,
	getTranscript: state.getTranscript,
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

function wireFetch(recording: Recording | null, transcript: Transcript | null): void {
	state.getRecording.mockResolvedValue(recording);
	state.getTranscript.mockResolvedValue(transcript);
}

describe("useRecording", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it("refresh() fetches recording + transcript, populating both refs", async () => {
		wireFetch(rec("1", "done"), tx("t1", "1"));
		const r = useRecording("1");

		await r.refresh();

		expect(state.getRecording).toHaveBeenCalledWith("1");
		expect(state.getTranscript).toHaveBeenCalledWith("1");
		expect(r.recording.value).toEqual(rec("1", "done"));
		expect(r.transcript.value).toEqual(tx("t1", "1"));
		expect(r.loading.value).toBe(false);
		expect(r.error.value).toBeNull();
		r.stopPolling();
	});

	it("transcript is null when none returned", async () => {
		wireFetch(rec("1", "done"), null);
		const r = useRecording("1");

		await r.refresh();

		expect(r.transcript.value).toBeNull();
		r.stopPolling();
	});

	it("captures error and ends loading false on rejection", async () => {
		state.getRecording.mockRejectedValue(new Error("boom"));
		state.getTranscript.mockResolvedValue(null);
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
		state.getRecording.mockImplementation(() => {
			calls += 1;
			return Promise.resolve(rec("1", calls >= 3 ? "done" : "pending"));
		});
		state.getTranscript.mockResolvedValue(tx("t1", "1"));

		const r = useRecording("1");
		r.startPolling(1000);

		// initial refresh (1 getRecording), still pending
		await vi.advanceTimersByTimeAsync(0);
		expect(state.getRecording).toHaveBeenCalledTimes(1);
		expect(r.recording.value?.status).toBe("pending");

		// tick 1 -> 2nd fetch, still pending
		await vi.advanceTimersByTimeAsync(1000);
		expect(state.getRecording).toHaveBeenCalledTimes(2);
		expect(r.recording.value?.status).toBe("pending");

		// tick 2 -> 3rd fetch, becomes done -> interval cleared
		await vi.advanceTimersByTimeAsync(1000);
		expect(state.getRecording).toHaveBeenCalledTimes(3);
		expect(r.recording.value?.status).toBe("done");

		// further time must not trigger more fetches
		await vi.advanceTimersByTimeAsync(5000);
		expect(state.getRecording).toHaveBeenCalledTimes(3);
		r.stopPolling();
	});

	it("does not keep an interval when already terminal on first refresh", async () => {
		wireFetch(rec("1", "done"), tx("t1", "1"));
		const r = useRecording("1");

		r.startPolling(1000);
		await vi.advanceTimersByTimeAsync(0);
		expect(state.getRecording).toHaveBeenCalledTimes(1);

		await vi.advanceTimersByTimeAsync(10000);
		expect(state.getRecording).toHaveBeenCalledTimes(1);
		r.stopPolling();
	});

	it("guards against multiple concurrent intervals", async () => {
		wireFetch(rec("1", "pending"), null);
		const r = useRecording("1");

		r.startPolling(1000);
		r.startPolling(1000);
		await vi.advanceTimersByTimeAsync(0);
		// two initial refreshes would be 2; guarded second start must be a no-op
		expect(state.getRecording).toHaveBeenCalledTimes(1);

		await vi.advanceTimersByTimeAsync(1000);
		// single interval -> one more fetch
		expect(state.getRecording).toHaveBeenCalledTimes(2);
		r.stopPolling();
	});

	it("stopPolling clears the interval", async () => {
		wireFetch(rec("1", "pending"), null);
		const r = useRecording("1");

		r.startPolling(1000);
		await vi.advanceTimersByTimeAsync(0);
		expect(state.getRecording).toHaveBeenCalledTimes(1);

		r.stopPolling();
		await vi.advanceTimersByTimeAsync(10000);
		expect(state.getRecording).toHaveBeenCalledTimes(1);
	});

	it("onScopeDispose stops polling when the scope is disposed", async () => {
		wireFetch(rec("1", "pending"), null);
		const scope = effectScope();
		let r!: ReturnType<typeof useRecording>;
		scope.run(() => {
			r = useRecording("1");
			r.startPolling(1000);
		});

		await vi.advanceTimersByTimeAsync(0);
		expect(state.getRecording).toHaveBeenCalledTimes(1);

		scope.stop();
		await vi.advanceTimersByTimeAsync(10000);
		expect(state.getRecording).toHaveBeenCalledTimes(1);
	});
});
