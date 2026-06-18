import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { useRecordings } from "~/composables/useRecordings";
import type { Recording } from "~/types/directus";

const state = vi.hoisted(() => ({
	request: vi.fn(),
	readItems: vi.fn((collection: string, query: unknown) => ({ collection, query })),
}));

mockNuxtImport("useDirectus", () => () => ({ client: { request: state.request } }));

vi.mock("@directus/sdk", () => ({
	readItems: (collection: string, query: unknown) => state.readItems(collection, query),
}));

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

describe("useRecordings", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		state.readItems.mockImplementation((collection: string, query: unknown) => ({ collection, query }));
	});

	it("list() queries recordings with default sort/limit and no filter, storing rows", async () => {
		const rows = [row("1"), row("2")];
		state.request.mockResolvedValue(rows);
		const r = useRecordings();
		expect(r.items.value).toEqual([]);

		const out = await r.list();

		expect(state.readItems).toHaveBeenCalledTimes(1);
		const [collection, query] = state.readItems.mock.calls[0];
		expect(collection).toBe("recordings");
		expect(query.sort).toEqual(["-date_created"]);
		expect(query.limit).toBe(100);
		expect(query.filter ?? {}).toEqual({});
		expect(state.request).toHaveBeenCalledTimes(1);
		expect(r.items.value).toEqual(rows);
		expect(out).toEqual(rows);
		expect(r.pending.value).toBe(false);
		expect(r.error.value).toBeNull();
	});

	it("list({ search }) adds a title _icontains filter", async () => {
		state.request.mockResolvedValue([]);
		const r = useRecordings();
		await r.list({ search: "befund" });
		const [, query] = state.readItems.mock.calls[0];
		expect(query.filter.title._icontains).toBe("befund");
		expect(query.filter.date_created).toBeUndefined();
	});

	it("list({ since }) adds a date_created _gte filter", async () => {
		state.request.mockResolvedValue([]);
		const r = useRecordings();
		await r.list({ since: "2026-06-01" });
		const [, query] = state.readItems.mock.calls[0];
		expect(query.filter.date_created._gte).toBe("2026-06-01");
		expect(query.filter.title).toBeUndefined();
	});

	it("list({ search, since }) includes both filter keys", async () => {
		state.request.mockResolvedValue([]);
		const r = useRecordings();
		await r.list({ search: "x", since: "2026-06-01" });
		const [, query] = state.readItems.mock.calls[0];
		expect(query.filter.title._icontains).toBe("x");
		expect(query.filter.date_created._gte).toBe("2026-06-01");
	});

	it("empty search string is ignored", async () => {
		state.request.mockResolvedValue([]);
		const r = useRecordings();
		await r.list({ search: "" });
		const [, query] = state.readItems.mock.calls[0];
		expect(query.filter ?? {}).toEqual({});
	});

	it("on request rejection sets error string, leaves items, ends pending false", async () => {
		state.request.mockRejectedValue(new Error("boom"));
		const r = useRecordings();
		const out = await r.list();
		expect(r.error.value).toBeTypeOf("string");
		expect(r.error.value).not.toBeNull();
		expect(r.items.value).toEqual([]);
		expect(out).toEqual([]);
		expect(r.pending.value).toBe(false);
	});

	it("refresh() calls list with no opts", async () => {
		const rows = [row("1")];
		state.request.mockResolvedValue(rows);
		const r = useRecordings();
		await r.refresh();
		const [, query] = state.readItems.mock.calls[0];
		expect(query.filter ?? {}).toEqual({});
		expect(r.items.value).toEqual(rows);
	});
});
