import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { createDirectusStore } from "~/data/directusStore";

const state = vi.hoisted(() => ({
	request: vi.fn(),
	uploadFiles: vi.fn((data: unknown) => ({ kind: "upload", data })),
	createItem: vi.fn((collection: string, payload: unknown) => ({ kind: "create", collection, payload })),
	readItems: vi.fn((collection: string, query: unknown) => ({ kind: "readItems", collection, query })),
}));

mockNuxtImport("useDirectus", () => () => ({
	client: { request: state.request },
	assetUrl: (id: string) => `http://d/assets/${id}`,
}));

vi.mock("@directus/sdk", () => ({
	uploadFiles: (data: unknown) => state.uploadFiles(data),
	createItem: (collection: string, payload: unknown) => state.createItem(collection, payload),
	readItems: (collection: string, query: unknown) => state.readItems(collection, query),
	readItem: (collection: string, id: string) => ({ kind: "readItem", collection, id }),
	updateItem: (collection: string, id: string, patch: unknown) => ({ kind: "update", collection, id, patch }),
	deleteItem: (collection: string, id: string) => ({ kind: "delete", collection, id }),
}));

const input = {
	title: "Visite",
	language: "de",
	profile: "default",
	source: "batch" as const,
	status: "pending" as const,
	duration_seconds: 42,
};

describe("createDirectusStore", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("lists recordings sorted, limited and filtered", async () => {
		state.request.mockResolvedValueOnce([{ id: "r1" }]);
		const rows = await createDirectusStore().listRecordings({ search: "abc", since: "2026-01-01" });
		expect(rows).toEqual([{ id: "r1" }]);
		expect(state.readItems).toHaveBeenCalledWith("recordings", {
			sort: ["-date_created"],
			limit: 100,
			filter: { title: { _icontains: "abc" }, date_created: { _gte: "2026-01-01" } },
		});
	});

	it("lists recordings without a filter when no opts given", async () => {
		state.request.mockResolvedValueOnce([]);
		await createDirectusStore().listRecordings({});
		expect(state.readItems).toHaveBeenCalledWith("recordings", { sort: ["-date_created"], limit: 100 });
	});

	it("createRecording uploads the blob then creates with audio_file", async () => {
		state.request.mockResolvedValueOnce({ id: "file-1" }).mockResolvedValueOnce({ id: "rec-1" });
		const created = await createDirectusStore().createRecording(input, new Blob(["a"]), "Visite.webm");
		expect(created).toEqual({ id: "rec-1" });
		const fd = state.uploadFiles.mock.calls[0]![0] as FormData;
		expect((fd.get("file") as File).name).toBe("Visite.webm");
		expect(state.createItem).toHaveBeenCalledWith("recordings", { ...input, audio_file: "file-1" });
	});

	it("createRecording with a null blob skips the upload", async () => {
		state.request.mockResolvedValueOnce({ id: "rec-2" });
		await createDirectusStore().createRecording({ ...input, source: "live", status: "done" }, null, "x.webm");
		expect(state.uploadFiles).not.toHaveBeenCalled();
		const payload = state.createItem.mock.calls[0]![1] as Record<string, unknown>;
		expect(payload.audio_file).toBeUndefined();
		expect(payload.source).toBe("live");
	});

	it("getTranscript returns null when none exists", async () => {
		state.request.mockResolvedValueOnce([]);
		expect(await createDirectusStore().getTranscript("rec-1")).toBeNull();
		expect(state.readItems).toHaveBeenCalledWith("transcripts", { filter: { recording: { _eq: "rec-1" } }, limit: 1 });
	});

	it("createDocument uploads then creates with status pending", async () => {
		state.request.mockResolvedValueOnce({ id: "file-9" }).mockResolvedValueOnce({ id: "doc-1" });
		const doc = await createDirectusStore().createDocument({ title: "Brief" }, new Blob(["pdf"]), "brief.pdf");
		expect(doc).toEqual({ id: "doc-1" });
		const fd = state.uploadFiles.mock.calls[0]![0] as FormData;
		expect((fd.get("file") as File).name).toBe("brief.pdf");
		expect(state.createItem).toHaveBeenCalledWith("documents", { title: "Brief", file: "file-9", status: "pending" });
	});

	it("audioUrl returns null without a file", async () => {
		const store = createDirectusStore();
		expect(await store.audioUrl({ id: "r", title: "", language: "de", profile: "p", status: "done", source: "batch" })).toBeNull();
		expect(await store.audioUrl({ id: "r", title: "", language: "de", profile: "p", status: "done", source: "batch", audio_file: "f1" })).toBe("http://d/assets/f1");
	});
});
