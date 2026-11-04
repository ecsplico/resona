import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { useUpload } from "~/composables/useUpload";

const state = vi.hoisted(() => ({
	createRecording: vi.fn(),
}));

mockNuxtImport("useDataStore", () => () => ({ createRecording: state.createRecording }));

describe("useUpload", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("creates a recording with the blob and the chosen profile/language", async () => {
		state.createRecording.mockResolvedValueOnce({ id: "rec-1" });

		const u = useUpload();
		const blob = new Blob(["audio"], { type: "audio/webm" });
		const created = await u.saveRecording({
			blob,
			title: "Visite",
			language: "en",
			profile: "medical",
			transcribe: true,
			duration_seconds: 42,
		});

		expect(state.createRecording).toHaveBeenCalledTimes(1);
		const [input, audio, filename] = state.createRecording.mock.calls[0]!;
		expect(input).toEqual({
			title: "Visite",
			language: "en",
			profile: "medical",
			source: "batch",
			status: "pending",
			duration_seconds: 42,
		});
		expect(audio).toBe(blob);
		expect(filename).toBe("Visite.webm");
		expect(created).toEqual({ id: "rec-1" });
		expect(u.uploading.value).toBe(false);
		expect(u.error.value).toBeNull();
	});

	it("uses status done when transcribe is false and falls back to recording.webm without a title", async () => {
		state.createRecording.mockResolvedValueOnce({ id: "rec-2" });

		const u = useUpload();
		await u.saveRecording({
			blob: new Blob(["x"], { type: "audio/webm" }),
			title: "",
			language: "de",
			profile: "default",
			transcribe: false,
			duration_seconds: 5,
		});

		const [input, , filename] = state.createRecording.mock.calls[0]!;
		expect(input).toMatchObject({ status: "done" });
		expect(filename).toBe("recording.webm");
	});

	it("sets error and rethrows when the upload fails", async () => {
		state.createRecording.mockRejectedValueOnce(new Error("boom"));
		const u = useUpload();
		await expect(u.saveRecording({
			blob: new Blob(["x"], { type: "audio/webm" }),
			title: "T",
			language: "de",
			profile: "default",
			transcribe: true,
			duration_seconds: 1,
		})).rejects.toThrow("boom");
		expect(u.error.value).toBeTypeOf("string");
		expect(u.uploading.value).toBe(false);
	});
});
