import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ShareReceivedEvent } from "@capgo/capacitor-share-target";
import { listenForNativeShares, receiveNativeShare } from "~/utils/nativeShare";
import { SHARE_CACHE } from "~/utils/shareStash";

const { filesystem, shareTarget, cache, opened } = vi.hoisted(() => ({
	filesystem: { deleteFile: vi.fn(async () => undefined) },
	shareTarget: { addListener: vi.fn() },
	cache: { put: vi.fn(async () => undefined) },
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
const openCache = vi.fn(async () => cache);

function share(files: ShareReceivedEvent["files"], title = "", texts: string[] = []): ShareReceivedEvent {
	return { title, texts, files };
}

/** The stash `put` of the last call: key, the stored file and its headers. */
async function stashed(): Promise<{ key: string; body: Blob; filename: string; title: string }> {
	const [key, res] = cache.put.mock.calls.at(-1) as unknown as [string, Response];
	return {
		key,
		body: await res.blob(),
		filename: decodeURIComponent(res.headers.get("x-share-filename")!),
		title: decodeURIComponent(res.headers.get("x-share-title")!),
	};
}

describe("native share hand-over", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		vi.stubGlobal("fetch", fetchMock);
		vi.stubGlobal("caches", { open: openCache });
	});

	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it("stashes a shared voice memo as audio, opens the share page and deletes the plugin copy", async () => {
		const url = await receiveNativeShare(share([{ uri: "/data/cache/shared_files/share-1.tmp", name: "Memo.m4a", mimeType: "audio/mp4" }]), 7);

		expect(url).toBe("/share-target?kind=audio&n=7");
		expect(fetchMock).toHaveBeenCalledWith("https://localhost/_capacitor_file_/data/cache/shared_files/share-1.tmp");
		expect(openCache).toHaveBeenCalledWith(SHARE_CACHE);
		const s = await stashed();
		expect(s.key).toBe("/share-target/audio");
		expect(s.filename).toBe("Memo.m4a");
		expect(s.body.type).toBe("audio/mp4");
		expect(filesystem.deleteFile).toHaveBeenCalledWith({ path: "/data/cache/shared_files/share-1.tmp" });
	});

	it("stashes a PDF and a DOCX as documents, also when the sender gives no type", async () => {
		expect(await receiveNativeShare(share([{ uri: "/c/a.tmp", name: "Befund.pdf", mimeType: "application/pdf" }], "Befund Mai"), 1))
			.toBe("/share-target?kind=document&n=1");
		let s = await stashed();
		expect(s.key).toBe("/share-target/document");
		expect(s.title).toBe("Befund Mai");

		expect(await receiveNativeShare(share([{ uri: "/c/b.tmp", name: "Brief.docx", mimeType: "application/octet-stream" }]), 2))
			.toBe("/share-target?kind=document&n=2");
		s = await stashed();
		expect(s.filename).toBe("Brief.docx");
		expect(s.key).toBe("/share-target/document");
	});

	it("takes the first usable file and still deletes every copy", async () => {
		const url = await receiveNativeShare(share([
			{ uri: "/c/1.tmp", name: "clip.gif", mimeType: "image/gif" },
			{ uri: "/c/2.tmp", name: "Diktat.ogg", mimeType: "audio/ogg" },
		]), 3);

		expect(url).toBe("/share-target?kind=audio&n=3");
		expect((await stashed()).filename).toBe("Diktat.ogg");
		expect(filesystem.deleteFile).toHaveBeenCalledTimes(2);
	});

	it("reports an unusable or unreadable share on the share page and never deletes a content URI", async () => {
		expect(await receiveNativeShare(share([{ uri: "/c/x.tmp", name: "clip.gif", mimeType: "image/gif" }]))).toBe("/share-target?error=1");

		fetchMock.mockResolvedValueOnce(new Response("", { status: 404 }));
		expect(await receiveNativeShare(share([{ uri: "content://media/1", name: "a.m4a", mimeType: "audio/mp4" }]))).toBe("/share-target?error=1");
		expect(cache.put).not.toHaveBeenCalled();
		expect(filesystem.deleteFile).toHaveBeenCalledTimes(1);
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
		handler(share([{ uri: "/c/2.tmp", name: "b.pdf", mimeType: "application/pdf" }]));
		await vi.waitFor(() => expect(opened).toHaveBeenCalledTimes(2));
		expect(opened.mock.calls[0]![0]).toMatch(/^\/share-target\?kind=audio&n=/);
		expect(opened.mock.calls[1]![0]).toMatch(/^\/share-target\?kind=document&n=/);
	});
});
