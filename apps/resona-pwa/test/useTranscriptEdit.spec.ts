import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { useTranscriptEdit } from "~/composables/useTranscriptEdit";

const state = vi.hoisted(() => ({
	updateTranscript: vi.fn(),
	createTranscript: vi.fn(),
}));

mockNuxtImport("useDataStore", () => () => ({
	updateTranscript: state.updateTranscript,
	createTranscript: state.createTranscript,
}));

describe("useTranscriptEdit", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("optimistically applies the new text and persists via the store", async () => {
		state.updateTranscript.mockResolvedValueOnce(undefined);

		const edit = useTranscriptEdit("alt");
		await edit.saveEdit("t-1", "neu");

		expect(edit.text.value).toBe("neu");
		expect(state.updateTranscript).toHaveBeenCalledWith("t-1", { text: "neu" });
		expect(edit.error.value).toBeNull();
		expect(edit.saving.value).toBe(false);
	});

	it("rolls back to the previous text and surfaces an error on failure", async () => {
		state.updateTranscript.mockRejectedValueOnce(new Error("boom"));

		const edit = useTranscriptEdit("alt");
		await edit.saveEdit("t-1", "neu");

		expect(edit.text.value).toBe("alt");
		expect(edit.error.value).toBeTypeOf("string");
		expect(edit.error.value).toBeTruthy();
		expect(edit.saving.value).toBe(false);
	});

	it("rolls back to the last committed value, not the initial value", async () => {
		state.updateTranscript
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
		state.createTranscript.mockResolvedValueOnce(created);

		const edit = useTranscriptEdit("");
		const row = await edit.createTranscript("rec-1", "frisch");

		expect(state.createTranscript).toHaveBeenCalledWith("rec-1", { text: "frisch" });
		expect(row).toEqual(created);
		expect(edit.text.value).toBe("frisch");
		expect(edit.error.value).toBeNull();
	});

	it("createTranscript rolls back the text and reports an error on failure", async () => {
		state.createTranscript.mockRejectedValueOnce(new Error("nope"));

		const edit = useTranscriptEdit("alt");
		const row = await edit.createTranscript("rec-1", "neu");

		expect(row).toBeNull();
		expect(edit.text.value).toBe("alt");
		expect(edit.error.value).toBeTruthy();
	});
});
