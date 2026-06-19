import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import { useUpload } from "~/composables/useUpload";

const state = vi.hoisted(() => ({
	request: vi.fn(),
	uploadFiles: vi.fn((data: unknown) => ({ kind: "upload", data })),
	createItem: vi.fn((collection: string, payload: unknown) => ({ kind: "create", collection, payload })),
}));

mockNuxtImport("useDirectus", () => () => ({ client: { request: state.request } }));

vi.mock("@directus/sdk", () => ({
	uploadFiles: (data: unknown) => state.uploadFiles(data),
	createItem: (collection: string, payload: unknown) => state.createItem(collection, payload),
}));

describe("useUpload", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		state.uploadFiles.mockImplementation((data: unknown) => ({ kind: "upload", data }));
		state.createItem.mockImplementation((collection: string, payload: unknown) => ({ kind: "create", collection, payload }));
	});

	it("uploads the blob then creates a recording with the chosen profile/language", async () => {
		state.request
			.mockResolvedValueOnce({ id: "file-1" })
			.mockResolvedValueOnce({ id: "rec-1" });

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

		// upload happened with FormData carrying a "file" part
		expect(state.uploadFiles).toHaveBeenCalledTimes(1);
		const fd = state.uploadFiles.mock.calls[0]![0] as FormData;
		expect(fd).toBeInstanceOf(FormData);
		expect(fd.get("file")).toBeTruthy();

		// createItem payload carries the chosen language/profile (not hardcoded)
		expect(state.createItem).toHaveBeenCalledTimes(1);
		const [collection, payload] = state.createItem.mock.calls[0]!;
		expect(collection).toBe("recordings");
		expect(payload).toMatchObject({
			title: "Visite",
			audio_file: "file-1",
			language: "en",
			profile: "medical",
			source: "batch",
			status: "pending",
			duration_seconds: 42,
		});

		// upload must run before create
		expect(state.request).toHaveBeenCalledTimes(2);
		expect(created).toEqual({ id: "rec-1" });
	});

	it("uses status done when transcribe is false", async () => {
		state.request
			.mockResolvedValueOnce({ id: "file-2" })
			.mockResolvedValueOnce({ id: "rec-2" });

		const u = useUpload();
		await u.saveRecording({
			blob: new Blob(["x"], { type: "audio/webm" }),
			title: "Memo",
			language: "de",
			profile: "default",
			transcribe: false,
			duration_seconds: 5,
		});

		const [, payload] = state.createItem.mock.calls[0]!;
		expect(payload).toMatchObject({ status: "done" });
	});

	it("sets error and rethrows when the upload fails", async () => {
		state.request.mockRejectedValueOnce(new Error("boom"));
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
