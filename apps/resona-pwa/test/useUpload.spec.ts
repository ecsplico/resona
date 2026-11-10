import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { useUpload } from "~/composables/useUpload";

const state = vi.hoisted(() => ({
	createRecording: vi.fn(),
}));

mockNuxtImport("useDataStore", () => () => ({ createRecording: state.createRecording }));

const probeDuration = vi.hoisted(() => vi.fn());
vi.mock("~/utils/audioFile", async importOriginal => ({
	...await importOriginal<typeof import("~/utils/audioFile")>(),
	probeDuration,
}));

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
			title_auto: false,
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

	it("reads the real duration for an upload that arrives without one", async () => {
		state.createRecording.mockResolvedValueOnce({ id: "rec-3" });
		probeDuration.mockResolvedValueOnce(125.4);

		const u = useUpload();
		await u.saveRecording({
			blob: new File(["audio"], "Memo.m4a", { type: "audio/mp4" }),
			title: "Memo",
			language: "de",
			profile: "default",
			transcribe: true,
			duration_seconds: 0,
		});

		expect(probeDuration).toHaveBeenCalledTimes(1);
		expect(state.createRecording.mock.calls[0]![0]).toMatchObject({ duration_seconds: 125.4 });
	});

	it("does not probe when the recorder already knows the duration", async () => {
		state.createRecording.mockResolvedValueOnce({ id: "rec-4" });
		await useUpload().saveRecording({
			blob: new Blob(["x"], { type: "audio/webm" }),
			title: "T",
			language: "de",
			profile: "default",
			transcribe: true,
			duration_seconds: 9,
		});
		expect(probeDuration).not.toHaveBeenCalled();
	});

	it("stores an untyped phone upload with a playable audio type and matching extension", async () => {
		state.createRecording.mockResolvedValueOnce({ id: "rec-5" });
		probeDuration.mockResolvedValueOnce(3);
		const file = new File(["audio"], "Voice 001.m4a", { type: "" });

		await useUpload().saveRecording({
			blob: file,
			title: "Voice 001",
			language: "de",
			profile: "default",
			transcribe: true,
			duration_seconds: 0,
		});

		const [, audio, filename] = state.createRecording.mock.calls[0]!;
		expect((audio as Blob).type).toBe("audio/mp4");
		expect(filename).toBe("Voice 001.m4a");
	});

	it("uses the shared file's original name for the type when the blob carries none", async () => {
		state.createRecording.mockResolvedValueOnce({ id: "rec-6" });
		probeDuration.mockResolvedValueOnce(3);

		await useUpload().saveRecording({
			blob: new Blob(["audio"], { type: "application/octet-stream" }),
			title: "Geteilt",
			language: "de",
			profile: "default",
			transcribe: true,
			duration_seconds: 0,
			sourceName: "PTT-2026.aac",
		});

		const [, audio, filename] = state.createRecording.mock.calls[0]!;
		expect((audio as Blob).type).toBe("audio/aac");
		expect(filename).toBe("Geteilt.aac");
	});
});
