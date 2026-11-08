import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { useRecordings } from "~/composables/useRecordings";
import type { Recording } from "~/types/directus";

const state = vi.hoisted(() => ({
	listRecordings: vi.fn(),
}));

mockNuxtImport("useDataStore", () => () => ({ listRecordings: state.listRecordings }));

function row(id: string): Recording {
	return {
		id,
		title: `rec-${id}`,
		language: "de",
		profile: "default",
		status: "done",
		source: "batch",
	};
}

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (reason: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

describe("useRecordings", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		clearNuxtState([...LIST_STATE_KEYS]);
	});

	it("list() loads recordings from the store and stores the rows", async () => {
		const rows = [row("1"), row("2")];
		state.listRecordings.mockResolvedValue(rows);
		const r = useRecordings();
		expect(r.items.value).toEqual([]);

		const out = await r.list();

		expect(state.listRecordings).toHaveBeenCalledTimes(1);
		expect(r.items.value).toEqual(rows);
		expect(out).toEqual(rows);
		expect(r.pending.value).toBe(false);
		expect(r.error.value).toBeNull();
	});

	it("list({ search, since }) passes both filters to the store", async () => {
		state.listRecordings.mockResolvedValue([]);
		const r = useRecordings();
		await r.list({ search: "x", since: "2026-06-01" });
		expect(state.listRecordings).toHaveBeenCalledWith({ search: "x", since: "2026-06-01" });
	});

	it("on store rejection sets error string, leaves items, ends pending false", async () => {
		state.listRecordings.mockRejectedValue(new Error("boom"));
		const r = useRecordings();
		const out = await r.list();
		expect(r.error.value).toBeTypeOf("string");
		expect(r.error.value).not.toBeNull();
		expect(r.items.value).toEqual([]);
		expect(out).toEqual([]);
		expect(r.pending.value).toBe(false);
	});

	it("refresh() re-issues the most recent list() filters", async () => {
		state.listRecordings.mockResolvedValue([]);
		const r = useRecordings();
		await r.list({ search: "x", since: "2026-06-01" });
		await r.refresh();
		expect(state.listRecordings).toHaveBeenCalledTimes(2);
		expect(state.listRecordings.mock.calls[1]![0]).toEqual({ search: "x", since: "2026-06-01" });
	});

	it("refresh() before any list() loads without a filter", async () => {
		const rows = [row("1")];
		state.listRecordings.mockResolvedValue(rows);
		const r = useRecordings();
		await r.refresh();
		const [opts] = state.listRecordings.mock.calls[0]!;
		expect(opts.search).toBeUndefined();
		expect(opts.since).toBeUndefined();
		expect(r.items.value).toEqual(rows);
	});

	it("a late response from an older list() does not overwrite the newer one", async () => {
		const a = deferred<Recording[]>();
		const b = deferred<Recording[]>();
		state.listRecordings
			.mockImplementationOnce(() => a.promise)
			.mockImplementationOnce(() => b.promise);
		const r = useRecordings();
		const outA = r.list();
		const outB = r.list();

		b.resolve([row("b")]);
		await outB;
		a.resolve([row("a")]);
		await outA;

		expect(r.items.value).toEqual([row("b")]);
		expect(await outA).toEqual([row("b")]);
		expect(r.pending.value).toBe(false);
	});

	it("a late error from an older list() does not set error", async () => {
		const a = deferred<Recording[]>();
		const b = deferred<Recording[]>();
		state.listRecordings
			.mockImplementationOnce(() => a.promise)
			.mockImplementationOnce(() => b.promise);
		const r = useRecordings();
		const outA = r.list();
		const outB = r.list();

		b.resolve([row("b")]);
		await outB;
		a.reject(new Error("boom"));
		await outA;

		expect(r.error.value).toBeNull();
		expect(r.items.value).toEqual([row("b")]);
		expect(r.pending.value).toBe(false);
	});

	it("stays pending while the newest list() is in flight after an older one settles", async () => {
		const a = deferred<Recording[]>();
		const b = deferred<Recording[]>();
		state.listRecordings
			.mockImplementationOnce(() => a.promise)
			.mockImplementationOnce(() => b.promise);
		const r = useRecordings();
		const outA = r.list();
		const outB = r.list();

		a.resolve([row("a")]);
		await outA;
		expect(r.pending.value).toBe(true);
		expect(r.items.value).toEqual([]);

		b.resolve([row("b")]);
		await outB;
		expect(r.pending.value).toBe(false);
		expect(r.items.value).toEqual([row("b")]);
	});
});
