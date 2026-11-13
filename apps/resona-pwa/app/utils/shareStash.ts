import { fileExtension, resolveAudioMime } from "~/utils/audioFile";

/**
 * The hand-over between whatever received a share and `pages/share-target.vue`,
 * through the Cache API. The service worker (`public/share-target-handler.js`,
 * audio only, keeps its own copy of these names) stores one file under
 * `WEB_SHARE_KEY` (`?kind=audio`); the Capacitor app (`utils/nativeShare.ts`)
 * stores the files of one share under `shareItemKey(0…n-1)` (`?count=n`). The page
 * reads them back and deletes them.
 */
export const SHARE_CACHE = "resona-share-target-v1";

export type ShareKind = "audio" | "document";

export const WEB_SHARE_KEY = "/share-target/audio";

export const DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

/** Document types the hub converts, by extension. */
const DOCUMENT_MIME_BY_EXTENSION: Record<string, string> = {
	pdf: "application/pdf",
	png: "image/png",
	jpg: "image/jpeg",
	jpeg: "image/jpeg",
	tif: "image/tiff",
	tiff: "image/tiff",
	docx: DOCX_MIME,
};

const DOCUMENT_MIMES = new Set(Object.values(DOCUMENT_MIME_BY_EXTENSION));

/** The document MIME type of a shared file (reported type, else its extension), or null. */
export function documentMime(reportedType: string, name = ""): string | null {
	const base = reportedType.split(";")[0]!.trim().toLowerCase();
	if (DOCUMENT_MIMES.has(base)) {
		return base;
	}
	return DOCUMENT_MIME_BY_EXTENSION[fileExtension(name)] ?? null;
}

/** Whether a shared file becomes a recording or a document; null when Resona takes neither. */
export function shareKind(reportedType: string, name = ""): ShareKind | null {
	if (resolveAudioMime(reportedType, name).startsWith("audio/")) {
		return "audio";
	}
	return documentMime(reportedType, name) ? "document" : null;
}

/** One shared file as the share-target page processes it. */
export interface SharedFile {
	kind: ShareKind;
	blob: Blob;
	filename: string;
	title: string;
}

/** Stash key of the `index`-th file of a share from the Capacitor app (`?count=`). */
export function shareItemKey(index: number): string {
	return `/share-target/item/${index}`;
}

const ITEM_PREFIX = "/share-target/item/";

/**
 * Puts the files of one share under `shareItemKey(0…n-1)`, replacing every item of an
 * older share. `title` (the sender's subject) is kept with each file.
 */
export async function stashShares(files: Omit<SharedFile, "title">[], title: string): Promise<void> {
	const cache = await caches.open(SHARE_CACHE);
	for (const request of await cache.keys()) {
		if (new URL(request.url).pathname.startsWith(ITEM_PREFIX)) {
			await cache.delete(request);
		}
	}
	for (const [index, file] of files.entries()) {
		const headers = new Headers();
		headers.set("content-type", file.blob.type || "application/octet-stream");
		headers.set("x-share-kind", file.kind);
		headers.set("x-share-filename", encodeURIComponent(file.filename));
		headers.set("x-share-title", encodeURIComponent(title));
		await cache.put(shareItemKey(index), new Response(file.blob, { headers }));
	}
}

/**
 * Reads and deletes (one-shot, so a reload does not import twice) the stashed files:
 * `count` items of an app share, else the single audio file of the web share target.
 */
export async function takeShares(count: number | null): Promise<SharedFile[]> {
	const cache = await caches.open(SHARE_CACHE);
	const entries: { key: string; kind: ShareKind | null }[] = count === null
		? [{ key: WEB_SHARE_KEY, kind: "audio" }]
		: Array.from({ length: count }, (_, index) => ({ key: shareItemKey(index), kind: null }));
	const files: SharedFile[] = [];
	for (const entry of entries) {
		const res = await cache.match(entry.key);
		if (!res) continue;
		await cache.delete(entry.key);
		files.push({
			kind: entry.kind ?? (res.headers.get("x-share-kind") === "document" ? "document" : "audio"),
			blob: await res.blob(),
			filename: decodeURIComponent(res.headers.get("x-share-filename") || ""),
			title: decodeURIComponent(res.headers.get("x-share-title") || ""),
		});
	}
	return files;
}
