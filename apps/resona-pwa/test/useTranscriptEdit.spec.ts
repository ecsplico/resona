import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { useTranscriptEdit } from "~/composables/useTranscriptEdit";

const state = vi.hoisted(() => ({
	request: vi.fn(),
	updateItem: vi.fn((collection: string, id: string, payload: unknown) => ({ kind: "update", collection, id, payload })),
}));

mockNuxtImport("useDirectus", () => () => ({ client: { request: state.request } }));

vi.mock("@directus/sdk", () => ({
	updateItem: (collection: string, id: string, payload: unknown) => state.updateItem(collection, id, payload),
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
});
