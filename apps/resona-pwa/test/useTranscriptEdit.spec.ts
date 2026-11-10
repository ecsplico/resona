import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { useTranscriptEdit } from "~/composables/useTranscriptEdit";

const state = vi.hoisted(() => ({
	updateTranscript: vi.fn(),
	createTranscript: vi.fn(),
	recordCorrection: vi.fn(),
}));

mockNuxtImport("useDataStore", () => () => ({
	updateTranscript: state.updateTranscript,
	createTranscript: state.createTranscript,
	recordCorrection: state.recordCorrection,
}));

describe("useTranscriptEdit", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("persists via the store and commits, leaving the editor text to the user", async () => {
		state.updateTranscript.mockResolvedValueOnce(undefined);

		const edit = useTranscriptEdit("alt");
		edit.text.value = "neu und weiter getippt";
		expect(await edit.saveEdit("t-1", "neu")).toBe(true);

		expect(state.updateTranscript).toHaveBeenCalledWith("t-1", { text: "neu" });
		expect(edit.committed.value).toBe("neu");
		expect(edit.text.value).toBe("neu und weiter getippt");
		expect(edit.error.value).toBeNull();
		expect(edit.saving.value).toBe(false);
	});

	it("reports the error on failure and leaves the committed text alone", async () => {
		state.updateTranscript.mockRejectedValueOnce(new Error("boom"));

		const edit = useTranscriptEdit("alt");
		edit.text.value = "neu";
		expect(await edit.saveEdit("t-1", "neu")).toBe(false);

		expect(edit.text.value).toBe("neu");
		expect(edit.committed.value).toBe("alt");
		expect(edit.error.value).toBeTruthy();
		expect(edit.saving.value).toBe(false);
	});

	it("clears the error on the next successful save", async () => {
		state.updateTranscript
			.mockRejectedValueOnce(new Error("boom"))
			.mockResolvedValueOnce(undefined);

		const edit = useTranscriptEdit("a");
		await edit.saveEdit("t-1", "b");
		expect(edit.error.value).toBeTruthy();

		await edit.saveEdit("t-1", "b");
		expect(edit.committed.value).toBe("b");
		expect(edit.error.value).toBeNull();
	});

	it("stores the change as a correction after the save, without holding the save up", async () => {
		state.updateTranscript.mockResolvedValueOnce(undefined);
		state.recordCorrection.mockResolvedValueOnce(undefined);

		const edit = useTranscriptEdit("Hallo Welt");
		await edit.saveEdit("t-1", "Hallo schöne Welt", { correction: { recording: "rec-1", engine: "faster-whisper", language: "de" } });
		expect(edit.saving.value).toBe(false);

		await vi.waitFor(() => expect(state.recordCorrection).toHaveBeenCalledWith({
			transcript: "t-1",
			recording: "rec-1",
			before: "Hallo Welt",
			after: "Hallo schöne Welt",
			edits: [{ before: "", after: "schöne" }],
			engine: "faster-whisper",
			language: "de",
		}));
	});

	it("enqueue turns saving on at once and runs writes one after another", async () => {
		const order: string[] = [];
		let finishFirst!: () => void;
		const edit = useTranscriptEdit("");

		const first = edit.enqueue(async () => {
			order.push("first start");
			await new Promise<void>((resolve) => {
				finishFirst = resolve;
			});
			order.push("first end");
		});
		expect(edit.saving.value).toBe(true);
		const second = edit.enqueue(async () => {
			order.push("second");
		});
		await vi.waitFor(() => expect(order).toEqual(["first start"]));

		finishFirst();
		await Promise.all([first, second]);
		expect(order).toEqual(["first start", "first end", "second"]);
		expect(edit.saving.value).toBe(false);
	});

	it("a failed queued write does not stop the ones after it", async () => {
		const edit = useTranscriptEdit("");
		const failed = edit.enqueue(async () => {
			throw new Error("boom");
		});
		const next = edit.enqueue(async () => "weiter");

		await expect(failed).rejects.toThrow("boom");
		expect(await next).toBe("weiter");
		expect(edit.saving.value).toBe(false);
	});

	it("seed loads a persisted text as both editor text and committed baseline", () => {
		const edit = useTranscriptEdit("");
		edit.seed("geladen");
		expect(edit.text.value).toBe("geladen");
		expect(edit.committed.value).toBe("geladen");
	});

	it("createTranscript creates a row, commits its text and returns it", async () => {
		const created = { id: "t-new", recording: "rec-1", text: "frisch" };
		state.createTranscript.mockResolvedValueOnce(created);

		const edit = useTranscriptEdit("");
		const row = await edit.createTranscript("rec-1", "frisch");

		expect(state.createTranscript).toHaveBeenCalledWith("rec-1", { text: "frisch" });
		expect(row).toEqual(created);
		expect(edit.committed.value).toBe("frisch");
		expect(edit.error.value).toBeNull();
	});

	it("createTranscript reports an error on failure and commits nothing", async () => {
		state.createTranscript.mockRejectedValueOnce(new Error("nope"));

		const edit = useTranscriptEdit("alt");
		const row = await edit.createTranscript("rec-1", "neu");

		expect(row).toBeNull();
		expect(edit.committed.value).toBe("alt");
		expect(edit.error.value).toBeTruthy();
	});
});
