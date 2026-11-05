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

	it("keeps the text on failure, reports the error and leaves the committed text alone", async () => {
		state.updateTranscript.mockRejectedValueOnce(new Error("boom"));

		const edit = useTranscriptEdit("alt");
		await edit.saveEdit("t-1", "neu");

		expect(edit.text.value).toBe("neu");
		expect(edit.committed.value).toBe("alt");
		expect(edit.error.value).toBeTypeOf("string");
		expect(edit.error.value).toBeTruthy();
		expect(edit.saving.value).toBe(false);
	});

	it("commits on success and clears the error on the next successful save", async () => {
		state.updateTranscript
			.mockRejectedValueOnce(new Error("boom"))
			.mockResolvedValueOnce(undefined);

		const edit = useTranscriptEdit("a");
		await edit.saveEdit("t-1", "b");
		expect(edit.error.value).toBeTruthy();

		await edit.saveEdit("t-1", "b");
		expect(edit.text.value).toBe("b");
		expect(edit.committed.value).toBe("b");
		expect(edit.error.value).toBeNull();
	});

	it("seed loads a persisted text as both editor text and committed baseline", async () => {
		state.updateTranscript.mockRejectedValueOnce(new Error("boom"));

		const edit = useTranscriptEdit("");
		edit.seed("geladen");
		expect(edit.text.value).toBe("geladen");
		expect(edit.committed.value).toBe("geladen");

		await edit.saveEdit("t-1", "geladen und mehr");
		expect(edit.text.value).toBe("geladen und mehr");
		expect(edit.committed.value).toBe("geladen");
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

	it("createTranscript keeps the text and reports an error on failure", async () => {
		state.createTranscript.mockRejectedValueOnce(new Error("nope"));

		const edit = useTranscriptEdit("alt");
		const row = await edit.createTranscript("rec-1", "neu");

		expect(row).toBeNull();
		expect(edit.text.value).toBe("neu");
		expect(edit.committed.value).toBe("alt");
		expect(edit.error.value).toBeTruthy();
	});
});
