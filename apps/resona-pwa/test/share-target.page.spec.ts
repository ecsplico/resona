import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ref } from "vue";
import { flushPromises, type VueWrapper } from "@vue/test-utils";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import SharePage from "~/pages/share-target.vue";
import { useHubSettings } from "~/composables/useHubSettings";
import { useSettingsDrawer } from "~/composables/useShell";
import { stashShares, WEB_SHARE_KEY, type ShareKind } from "~/utils/shareStash";

const saveRecording = vi.hoisted(() => vi.fn());
mockNuxtImport("useUpload", () => () => ({ uploading: ref(false), error: ref(null), saveRecording }));
mockNuxtImport("useProfiles", () => () => ({ list: vi.fn(async () => ["default"]) }));
const createDocument = vi.hoisted(() => vi.fn());
mockNuxtImport("useDataStore", () => () => ({ createDocument }));
const route = vi.hoisted(() => ({ query: {} as Record<string, string>, fullPath: "/share-target" }));
mockNuxtImport("useRoute", () => () => route);
const navigateTo = vi.hoisted(() => vi.fn());
mockNuxtImport("navigateTo", () => navigateTo);

/** Cache API stand-in: entries by path. */
const entries = new Map<string, Response>();
const path = (key: string | Request) => new URL(typeof key === "string" ? key : key.url, "https://localhost").pathname;
const cache = {
	put: async (key: string, res: Response) => void entries.set(path(key), res),
	match: vi.fn(async (key: string) => entries.get(path(key))?.clone()),
	delete: async (key: string | Request) => entries.delete(path(key)),
	keys: async () => [...entries.keys()].map(p => new Request(`https://localhost${p}`)),
};
const realCaches = Object.getOwnPropertyDescriptor(globalThis, "caches");

/** What the service worker stores for a web share: one audio file. */
function webShare(type: string, filename: string, title = ""): void {
	entries.set(WEB_SHARE_KEY, new Response(new Blob(["audio"], { type }), {
		// The service worker stores `file.type || "application/octet-stream"`.
		headers: { "content-type": type || "application/octet-stream", "x-share-filename": encodeURIComponent(filename), "x-share-title": encodeURIComponent(title) },
	}));
}

/** What the app stores for a share (`utils/nativeShare.ts`), and the page's query for it. */
async function appShare(files: [ShareKind, string, string][], title = "", skipped = 0): Promise<void> {
	await stashShares(files.map(([kind, filename, type]) => ({ kind, filename, blob: new Blob([filename], { type }) })), title);
	route.query = { count: String(files.length), n: "1", ...(skipped ? { skipped: String(skipped) } : {}) };
}

const sheet = (w: VueWrapper) => w.findComponent({ name: "SaveSheet" });

describe("share-target page", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		entries.clear();
		Object.defineProperty(globalThis, "caches", { value: { open: async () => cache }, configurable: true });
		useHubSettings().token = "tok";
		let recordings = 0;
		saveRecording.mockImplementation(async () => ({ id: `rec-${++recordings}` }));
		let documents = 0;
		createDocument.mockImplementation(async () => ({ id: `doc-${++documents}` }));
		route.query = { kind: "audio" };
	});

	afterEach(() => {
		if (realCaches) Object.defineProperty(globalThis, "caches", realCaches);
		else Reflect.deleteProperty(globalThis, "caches");
		useHubSettings().token = "";
	});

	it("blocks without a hub token, keeps the stashed file and opens the settings on demand", async () => {
		useHubSettings().token = "";
		webShare("audio/mp4", "Memo.m4a");
		const drawer = useSettingsDrawer();
		drawer.hide();
		const w = await mountSuspended(SharePage);
		await flushPromises();

		expect(w.find("[data-testid=\"share-token-missing\"]").text()).toContain("Einstellungen → Verbindung");
		expect(sheet(w).exists()).toBe(false);
		expect(cache.match).not.toHaveBeenCalled();
		expect(entries.size).toBe(1);

		await w.find("[data-testid=\"share-open-settings\"]").trigger("click");
		expect(drawer.open.value).toBe(true);
		drawer.hide();
		// A still-mounted blocked page would also resume when a later test enters a token.
		w.unmount();
	});

	it("resumes the import once the token is entered and takes the file only once", async () => {
		useHubSettings().token = "";
		webShare("audio/mp4", "Memo.m4a");
		const w = await mountSuspended(SharePage);
		await flushPromises();
		expect(sheet(w).exists()).toBe(false);

		useHubSettings().token = "tok";
		await flushPromises();
		expect(entries.size).toBe(0);
		expect(sheet(w).exists()).toBe(true);
		expect(w.find("[data-testid=\"share-token-missing\"]").exists()).toBe(false);
	});

	it("saves the shared file with its name, a derived audio type and a duration of 0 (probed by useUpload)", async () => {
		webShare("", "WhatsApp Audio.m4a");
		const w = await mountSuspended(SharePage);
		await flushPromises();
		expect(sheet(w).props("initialTitle")).toBe("WhatsApp Audio");
		sheet(w).vm.$emit("save", { title: "Gespräch", titleAuto: false, language: "de", profile: "default", transcribe: true });
		await flushPromises();

		expect(saveRecording).toHaveBeenCalledTimes(1);
		const arg = saveRecording.mock.calls[0]![0] as { blob: File; title: string; sourceName: string; duration_seconds: number };
		expect(arg.blob).toBeInstanceOf(File);
		expect(arg.blob.name).toBe("WhatsApp Audio.m4a");
		expect(arg.blob.type).toBe("audio/mp4");
		expect(arg.title).toBe("Gespräch");
		expect(arg.sourceName).toBe("WhatsApp Audio.m4a");
		expect(arg.duration_seconds).toBe(0);
		expect(navigateTo).toHaveBeenCalledWith("/recording/rec-1", { replace: true });
	});

	it("uploads a shared document from the app right away under its shared title, typed from its extension", async () => {
		await appShare([["document", "Brief.docx", "application/octet-stream"]], "Arztbrief");
		const w = await mountSuspended(SharePage);
		await flushPromises();

		expect(entries.size).toBe(0);
		expect(sheet(w).exists()).toBe(false);
		expect(saveRecording).not.toHaveBeenCalled();
		const [input, file, name] = createDocument.mock.calls[0]! as [{ title: string }, File, string];
		expect(input).toEqual({ title: "Arztbrief" });
		expect(name).toBe("Brief.docx");
		expect(file.type).toBe("application/vnd.openxmlformats-officedocument.wordprocessingml.document");
		expect(navigateTo).toHaveBeenCalledWith("/documents/doc-1", { replace: true });
	});

	it("keeps a shared document until the token is entered", async () => {
		useHubSettings().token = "";
		await appShare([["document", "Befund.pdf", "application/pdf"]]);
		const w = await mountSuspended(SharePage);
		await flushPromises();
		expect(cache.match).not.toHaveBeenCalled();
		expect(createDocument).not.toHaveBeenCalled();

		useHubSettings().token = "tok";
		await flushPromises();
		expect(createDocument.mock.calls[0]![0]).toEqual({ title: "Befund" });
		w.unmount();
	});

	it("uploads several documents under their file names and opens the documents list", async () => {
		await appShare([["document", "Befund.pdf", "application/pdf"], ["document", "Brief.docx", ""]], "Betreff");
		await mountSuspended(SharePage);
		await flushPromises();

		expect(createDocument.mock.calls.map(c => [c[0], c[2]])).toEqual([
			[{ title: "Befund" }, "Befund.pdf"],
			[{ title: "Brief" }, "Brief.docx"],
		]);
		expect(navigateTo).toHaveBeenCalledWith("/documents", { replace: true });
	});

	it("saves several recordings with one sheet, each under its file name, and opens the recordings list", async () => {
		await appShare([["audio", "Visite 1.m4a", "audio/mp4"], ["document", "Befund.pdf", "application/pdf"], ["audio", "Visite 2.ogg", "audio/ogg"]]);
		const w = await mountSuspended(SharePage);
		await flushPromises();

		expect(createDocument).toHaveBeenCalledTimes(1);
		expect(sheet(w).props("count")).toBe(2);
		expect(sheet(w).find("[data-testid=\"title\"]").exists()).toBe(false);
		sheet(w).vm.$emit("save", { title: "13.11. 10:00", titleAuto: true, language: "en", profile: "default", transcribe: true });
		await flushPromises();

		expect(saveRecording.mock.calls.map((c) => {
			const a = c[0] as { title: string; titleAuto: boolean; language: string; sourceName: string };
			return [a.title, a.titleAuto, a.language, a.sourceName];
		})).toEqual([
			["Visite 1", false, "en", "Visite 1.m4a"],
			["Visite 2", false, "en", "Visite 2.ogg"],
		]);
		expect(navigateTo).toHaveBeenCalledWith("/", { replace: true });
	});

	it("lists the files that failed or were skipped instead of leaving the page", async () => {
		createDocument.mockRejectedValueOnce(new Error("Nicht unterstützt"));
		await appShare([["document", "Brief.docx", ""], ["document", "Befund.pdf", "application/pdf"]], "", 1);
		const w = await mountSuspended(SharePage);
		await flushPromises();

		expect(createDocument).toHaveBeenCalledTimes(2);
		const message = w.find("[data-testid=\"share-message\"]").text();
		expect(message).toContain("Brief.docx");
		expect(message).toContain("1 Datei hat ein Format, das Resona nicht übernimmt.");
		expect(navigateTo).not.toHaveBeenCalled();
	});

	it("discarding the recordings still keeps the documents of the share", async () => {
		await appShare([["audio", "a.m4a", "audio/mp4"], ["document", "Befund.pdf", "application/pdf"]]);
		const w = await mountSuspended(SharePage);
		await flushPromises();
		sheet(w).vm.$emit("discard");
		await flushPromises();

		expect(saveRecording).not.toHaveBeenCalled();
		expect(createDocument).toHaveBeenCalledTimes(1);
		expect(navigateTo).toHaveBeenCalledWith("/documents", { replace: true });
	});
});
