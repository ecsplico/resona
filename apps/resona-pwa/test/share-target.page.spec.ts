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
const createDocument = vi.hoisted(() => vi.fn());
mockNuxtImport("useDataStore", () => () => ({ createDocument }));
const route = vi.hoisted(() => ({ query: {} as Record<string, string>, fullPath: "/share-target" }));
mockNuxtImport("useRoute", () => () => route);
const navigateTo = vi.hoisted(() => vi.fn());
mockNuxtImport("navigateTo", () => navigateTo);

const cache = vi.hoisted(() => ({
	match: vi.fn(),
	delete: vi.fn(async () => true),
}));
const realCaches = Object.getOwnPropertyDescriptor(globalThis, "caches");

function stash(type: string, filename: string, title = ""): void {
	cache.match.mockResolvedValue(new Response(new Blob(["audio"], { type }), {
		// The service worker stores `file.type || "application/octet-stream"`.
		headers: { "content-type": type || "application/octet-stream", "x-share-filename": encodeURIComponent(filename), "x-share-title": encodeURIComponent(title) },
	}));
}

describe("share-target page", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		Object.defineProperty(globalThis, "caches", { value: { open: async () => cache }, configurable: true });
		useHubSettings().token = "tok";
		saveRecording.mockResolvedValue({ id: "rec-1" });
		createDocument.mockResolvedValue({ id: "doc-1" });
		route.query = { kind: "audio" };
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

	it("uploads a shared document from the app right away under its shared title, typed from its extension", async () => {
		route.query = { kind: "document", n: "1" };
		stash("application/octet-stream", "Brief.docx", "Arztbrief");
		const w = await mountSuspended(SharePage);
		await flushPromises();

		expect(cache.match).toHaveBeenCalledWith("/share-target/document");
		expect(cache.delete).toHaveBeenCalledWith("/share-target/document");
		expect(w.findComponent({ name: "SaveSheet" }).exists()).toBe(false);
		expect(saveRecording).not.toHaveBeenCalled();
		const [input, file, name] = createDocument.mock.calls[0]! as [{ title: string }, File, string];
		expect(input).toEqual({ title: "Arztbrief" });
		expect(name).toBe("Brief.docx");
		expect(file.type).toBe("application/vnd.openxmlformats-officedocument.wordprocessingml.document");
		expect(navigateTo).toHaveBeenCalledWith("/documents/doc-1", { replace: true });
	});

	it("keeps a shared document until the token is entered", async () => {
		useHubSettings().token = "";
		route.query = { kind: "document" };
		stash("application/pdf", "Befund.pdf");
		const w = await mountSuspended(SharePage);
		await flushPromises();
		expect(cache.match).not.toHaveBeenCalled();
		expect(createDocument).not.toHaveBeenCalled();

		useHubSettings().token = "tok";
		await flushPromises();
		expect(createDocument.mock.calls[0]![0]).toEqual({ title: "Befund" });
		w.unmount();
	});
});
