import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ShareReceivedEvent } from "@capgo/capacitor-share-target";
import { listenForNativeShares, receiveNativeShare } from "~/utils/nativeShare";
import { SHARE_CACHE, takeShares } from "~/utils/shareStash";

const { filesystem, shareTarget, opened } = vi.hoisted(() => ({
	filesystem: { deleteFile: vi.fn(async () => undefined) },
	shareTarget: { addListener: vi.fn() },
	opened: vi.fn(),
}));

vi.mock("@capacitor/core", () => ({
	Capacitor: {
		isNativePlatform: () => true,
		isPluginAvailable: () => true,
		convertFileSrc: (uri: string) => `https://localhost/_capacitor_file_${uri}`,
	},
}));
vi.mock("@capacitor/filesystem", () => ({ Filesystem: filesystem }));
vi.mock("@capgo/capacitor-share-target", () => ({ CapacitorShareTarget: shareTarget }));

const fetchMock = vi.fn(async () => new Response("bytes"));

/** Cache API stand-in: entries by path, as `caches.open(...)` gives them. */
function memoryCache() {
	const entries = new Map<string, Response>();
	const path = (key: string | Request) => new URL(typeof key === "string" ? key : key.url, "https://localhost").pathname;
	return {
		entries,
		put: vi.fn(async (key: string, res: Response) => void entries.set(path(key), res)),
		match: vi.fn(async (key: string) => entries.get(path(key))?.clone()),
		delete: vi.fn(async (key: string | Request) => entries.delete(path(key))),
		keys: vi.fn(async () => [...entries.keys()].map(p => new Request(`https://localhost${p}`))),
	};
}

let cache = memoryCache();
const openCache = vi.fn(async () => cache);

function share(files: ShareReceivedEvent["files"], title = "", texts: string[] = []): ShareReceivedEvent {
	return { title, texts, files };
}

/** The `count` of a share-page URL. */
function count(url: string): number {
	return Number(new URL(url, "https://localhost").searchParams.get("count"));
}

describe("native share hand-over", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		cache = memoryCache();
		vi.stubGlobal("fetch", fetchMock);
		vi.stubGlobal("caches", { open: openCache });
	});

	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it("hands a shared voice memo to the share page as audio and deletes the plugin copy", async () => {
		const url = await receiveNativeShare(share([{ uri: "/data/cache/shared_files/share-1.tmp", name: "Memo.m4a", mimeType: "audio/mp4" }], "Visite"), 7);

		expect(url).toBe("/share-target?count=1&n=7");
		expect(fetchMock).toHaveBeenCalledWith("https://localhost/_capacitor_file_/data/cache/shared_files/share-1.tmp");
		expect(openCache).toHaveBeenCalledWith(SHARE_CACHE);
		const [file] = await takeShares(count(url));
		expect(file).toMatchObject({ kind: "audio", filename: "Memo.m4a", title: "Visite" });
		expect(file!.blob.type).toBe("audio/mp4");
		expect(filesystem.deleteFile).toHaveBeenCalledWith({ path: "/data/cache/shared_files/share-1.tmp" });
		// One-shot: a reload of the share page finds nothing.
		expect(await takeShares(count(url))).toEqual([]);
	});

	it("hands over every usable file of a share in order, a DOCX without type as document, and reports the rest as skipped", async () => {
		const url = await receiveNativeShare(share([
			{ uri: "/c/1.tmp", name: "Befund.pdf", mimeType: "application/pdf" },
			{ uri: "/c/2.tmp", name: "clip.gif", mimeType: "image/gif" },
			{ uri: "/c/3.tmp", name: "Brief.docx", mimeType: "application/octet-stream" },
			{ uri: "/c/4.tmp", name: "Diktat.ogg", mimeType: "audio/ogg" },
		]), 3);

		expect(url).toBe("/share-target?count=3&n=3&skipped=1");
		const files = await takeShares(count(url));
		expect(files.map(f => [f.kind, f.filename])).toEqual([
			["document", "Befund.pdf"],
			["document", "Brief.docx"],
			["audio", "Diktat.ogg"],
		]);
		expect(filesystem.deleteFile).toHaveBeenCalledTimes(4);
	});

	it("replaces the files of an older share that was never opened", async () => {
		await receiveNativeShare(share([
			{ uri: "/c/1.tmp", name: "a.m4a", mimeType: "audio/mp4" },
			{ uri: "/c/2.tmp", name: "b.m4a", mimeType: "audio/mp4" },
		]));
		const url = await receiveNativeShare(share([{ uri: "/c/3.tmp", name: "c.pdf", mimeType: "application/pdf" }]));

		expect(cache.entries.size).toBe(1);
		expect((await takeShares(count(url))).map(f => f.filename)).toEqual(["c.pdf"]);
	});

	it("counts an unreadable file as skipped and reports a share with no usable file, never deleting a content URI", async () => {
		fetchMock.mockResolvedValueOnce(new Response("", { status: 404 }));
		expect(await receiveNativeShare(share([
			{ uri: "/c/1.tmp", name: "a.m4a", mimeType: "audio/mp4" },
			{ uri: "/c/2.tmp", name: "b.m4a", mimeType: "audio/mp4" },
		]), 5)).toBe("/share-target?count=1&n=5&skipped=1");

		expect(await receiveNativeShare(share([{ uri: "/c/x.tmp", name: "clip.gif", mimeType: "image/gif" }]))).toBe("/share-target?error=1");
		fetchMock.mockResolvedValueOnce(new Response("", { status: 404 }));
		expect(await receiveNativeShare(share([{ uri: "content://media/1", name: "a.m4a", mimeType: "audio/mp4" }]))).toBe("/share-target?error=1");
		expect(filesystem.deleteFile).not.toHaveBeenCalledWith({ path: "content://media/1" });
	});

	it("sends a share without files to live dictation with its text as title, like the web share target", async () => {
		expect(await receiveNativeShare(share([], "", ["Visite Station 3"]))).toBe("/live?title=Visite%20Station%203");
		expect(await receiveNativeShare(share([]))).toBe("/live");
	});

	it("opens each received share in order", async () => {
		let handler: (event: ShareReceivedEvent) => void = () => undefined;
		shareTarget.addListener.mockImplementation(async (_name: string, fn: typeof handler) => {
			handler = fn;
			return { remove: async () => undefined };
		});
		await listenForNativeShares(async url => opened(url));
		expect(shareTarget.addListener).toHaveBeenCalledWith("shareReceived", expect.any(Function));

		handler(share([{ uri: "/c/1.tmp", name: "a.m4a", mimeType: "audio/mp4" }]));
		handler(share([{ uri: "/c/2.tmp", name: "b.pdf", mimeType: "application/pdf" }, { uri: "/c/3.tmp", name: "c.pdf", mimeType: "application/pdf" }]));
		await vi.waitFor(() => expect(opened).toHaveBeenCalledTimes(2));
		expect(opened.mock.calls[0]![0]).toMatch(/^\/share-target\?count=1&n=/);
		expect(opened.mock.calls[1]![0]).toMatch(/^\/share-target\?count=2&n=/);
	});
});
