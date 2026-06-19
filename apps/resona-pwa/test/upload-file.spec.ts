import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref } from "vue";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import IndexPage from "~/pages/index.vue";
import type { Recording } from "~/types/directus";

const items = vi.hoisted(() => ({ value: [] as Recording[] }));
const list = vi.hoisted(() => vi.fn(async () => []));
const refresh = vi.hoisted(() => vi.fn(async () => []));

mockNuxtImport("useRecordings", () => () => ({
	items: ref(items.value),
	pending: ref(false),
	error: ref(null),
	list,
	refresh,
}));

const saveRecording = vi.hoisted(() => vi.fn(async () => ({}) as Recording));
mockNuxtImport("useUpload", () => () => ({
	uploading: ref(false),
	error: ref(null),
	saveRecording,
}));

const profileList = vi.hoisted(() => vi.fn(async () => ["default"]));
mockNuxtImport("useProfiles", () => () => ({ list: profileList }));

mockNuxtImport("navigateTo", () => vi.fn());

describe("upload existing audio file", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		items.value = [];
	});

	it("runs the saveRecording path with the selected file as the blob", async () => {
		const w = await mountSuspended(IndexPage);

		const file = new File(["fake audio"], "interview.mp3", { type: "audio/mpeg" });
		const input = w.find("[data-testid=\"upload-input\"]");
		expect(input.exists()).toBe(true);

		Object.defineProperty(input.element, "files", {
			configurable: true,
			value: [file],
		});
		await input.trigger("change");

		// SaveSheet should now be open with the filename (sans extension) as title.
		const titleInput = w.find("[data-testid=\"title\"] input");
		expect((titleInput.element as HTMLInputElement).value).toBe("interview");

		await w.find("[data-testid=\"save\"]").trigger("click");
		await w.vm.$nextTick();

		expect(saveRecording).toHaveBeenCalledTimes(1);
		const arg = saveRecording.mock.calls[0]![0] as { blob: File; title: string };
		expect(arg.blob).toBeInstanceOf(File);
		expect(arg.blob.name).toBe("interview.mp3");
		expect(arg.blob.type).toBe("audio/mpeg");
		expect(arg.title).toBe("interview");
	});
});
