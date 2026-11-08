import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ref } from "vue";
import { flushPromises } from "@vue/test-utils";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import SharePage from "~/pages/share-target.vue";
import { useHubSettings } from "~/composables/useHubSettings";
import { useSettingsDrawer } from "~/composables/useShell";

const saveRecording = vi.hoisted(() => vi.fn());
mockNuxtImport("useUpload", () => () => ({ uploading: ref(false), error: ref(null), saveRecording }));
mockNuxtImport("useProfiles", () => () => ({ list: vi.fn(async () => ["default"]) }));
const navigateTo = vi.hoisted(() => vi.fn());
mockNuxtImport("navigateTo", () => navigateTo);

const cache = vi.hoisted(() => ({
	match: vi.fn(),
	delete: vi.fn(async () => true),
}));
const realCaches = Object.getOwnPropertyDescriptor(globalThis, "caches");

function stash(type: string, filename: string): void {
	cache.match.mockResolvedValue(new Response(new Blob(["audio"], { type }), {
		// The service worker stores `file.type || "application/octet-stream"`.
		headers: { "content-type": type || "application/octet-stream", "x-share-filename": encodeURIComponent(filename), "x-share-title": "" },
	}));
}

describe("share-target page", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		Object.defineProperty(globalThis, "caches", { value: { open: async () => cache }, configurable: true });
		useHubSettings().token = "tok";
		saveRecording.mockResolvedValue({ id: "rec-1" });
	});

	afterEach(() => {
		if (realCaches) Object.defineProperty(globalThis, "caches", realCaches);
		else Reflect.deleteProperty(globalThis, "caches");
		useHubSettings().token = "";
	});

	it("blocks without a hub token, keeps the stashed file and opens the settings on demand", async () => {
		useHubSettings().token = "";
		stash("audio/mp4", "Memo.m4a");
		const drawer = useSettingsDrawer();
		drawer.hide();
		const w = await mountSuspended(SharePage);
		await flushPromises();

		expect(w.find("[data-testid=\"share-token-missing\"]").text()).toContain("Einstellungen → Verbindung");
		expect(w.findComponent({ name: "SaveSheet" }).exists()).toBe(false);
		expect(cache.match).not.toHaveBeenCalled();
		expect(cache.delete).not.toHaveBeenCalled();

		await w.find("[data-testid=\"share-open-settings\"]").trigger("click");
		expect(drawer.open.value).toBe(true);
		drawer.hide();
		// A still-mounted blocked page would also resume when a later test enters a token.
		w.unmount();
	});

	it("resumes the import once the token is entered", async () => {
		useHubSettings().token = "";
		stash("audio/mp4", "Memo.m4a");
		const w = await mountSuspended(SharePage);
		await flushPromises();
		expect(w.findComponent({ name: "SaveSheet" }).exists()).toBe(false);

		useHubSettings().token = "tok";
		await flushPromises();
		expect(cache.delete).toHaveBeenCalledTimes(1);
		expect(w.findComponent({ name: "SaveSheet" }).exists()).toBe(true);
		expect(w.find("[data-testid=\"share-token-missing\"]").exists()).toBe(false);
	});

	it("saves the shared file with its name, a derived audio type and a duration of 0 (probed by useUpload)", async () => {
		stash("", "WhatsApp Audio.m4a");
		const w = await mountSuspended(SharePage);
		await flushPromises();
		w.findComponent({ name: "SaveSheet" }).vm.$emit("save", { title: "Gespräch", language: "de", profile: "default", transcribe: true });
		await flushPromises();

		expect(saveRecording).toHaveBeenCalledTimes(1);
		const arg = saveRecording.mock.calls[0]![0] as { blob: File; sourceName: string; duration_seconds: number };
		expect(arg.blob).toBeInstanceOf(File);
		expect(arg.blob.name).toBe("WhatsApp Audio.m4a");
		expect(arg.blob.type).toBe("audio/mp4");
		expect(arg.sourceName).toBe("WhatsApp Audio.m4a");
		expect(arg.duration_seconds).toBe(0);
		expect(navigateTo).toHaveBeenCalledWith("/recording/rec-1", { replace: true });
	});
});
