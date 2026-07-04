import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { useTranscriptEdit } from "~/composables/useTranscriptEdit";

const state = vi.hoisted(() => ({
	request: vi.fn(),
	updateItem: vi.fn((collection: string, id: string, payload: unknown) => ({ kind: "update", collection, id, payload })),
	createItem: vi.fn((collection: string, payload: unknown) => ({ kind: "create", collection, payload })),
}));

mockNuxtImport("useDirectus", () => () => ({ client: { request: state.request } }));

vi.mock("@directus/sdk", () => ({
	updateItem: (collection: string, id: string, payload: unknown) => state.updateItem(collection, id, payload),
	createItem: (collection: string, payload: unknown) => state.createItem(collection, payload),
}));

describe("useTranscriptEdit", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		state.updateItem.mockImplementation((collection: string, id: string, payload: unknown) => ({ kind: "update", collection, id, payload }));
	});

	it("optimistically applies the new text and persists via updateItem", async () => {
		state.request.mockResolvedValueOnce(undefined);

		const edit = useTranscriptEdit("alt");
		await edit.saveEdit("t-1", "neu");

		expect(edit.text.value).toBe("neu");
		expect(state.updateItem).toHaveBeenCalledWith("transcripts", "t-1", { text: "neu" });
		expect(state.request).toHaveBeenCalledTimes(1);
		expect(edit.error.value).toBeNull();
		expect(edit.saving.value).toBe(false);
	});

	it("rolls back to the previous text and surfaces an error on failure", async () => {
		state.request.mockRejectedValueOnce(new Error("boom"));

		const edit = useTranscriptEdit("alt");
		await edit.saveEdit("t-1", "neu");

		expect(edit.text.value).toBe("alt");
		expect(edit.error.value).toBeTypeOf("string");
		expect(edit.error.value).toBeTruthy();
		expect(edit.saving.value).toBe(false);
	});

	it("rolls back to the last committed value, not the initial value", async () => {
		state.request
			.mockResolvedValueOnce(undefined)
			.mockRejectedValueOnce(new Error("boom"));

		const edit = useTranscriptEdit("a");
		await edit.saveEdit("t-1", "b"); // commits "b"
		expect(edit.text.value).toBe("b");

		await edit.saveEdit("t-1", "c"); // fails -> revert to "b", not "a"
		expect(edit.text.value).toBe("b");
		expect(edit.error.value).toBeTruthy();
	});

	it("createTranscript creates a row and returns it, applying the text", async () => {
		const created = { id: "t-new", recording: "rec-1", text: "frisch" };
		state.request.mockResolvedValueOnce(created);

		const edit = useTranscriptEdit("");
		const row = await edit.createTranscript("rec-1", "frisch", "faster-whisper");

		expect(state.createItem).toHaveBeenCalledWith("transcripts", {
			recording: "rec-1",
			text: "frisch",
			engine: "faster-whisper",
		});
		expect(row).toEqual(created);
		expect(edit.text.value).toBe("frisch");
		expect(edit.error.value).toBeNull();
	});

	it("createTranscript rolls back the text and reports an error on failure", async () => {
		state.request.mockRejectedValueOnce(new Error("nope"));

		const edit = useTranscriptEdit("alt");
		const row = await edit.createTranscript("rec-1", "neu");

		expect(row).toBeNull();
		expect(edit.text.value).toBe("alt");
		expect(edit.error.value).toBeTruthy();
	});
});
