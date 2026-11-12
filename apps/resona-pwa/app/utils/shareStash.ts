import { fileExtension, resolveAudioMime } from "~/utils/audioFile";

/**
 * The hand-over between whatever received a share and `pages/share-target.vue`:
 * one shared file in the Cache API, one entry per kind. Written by the service
 * worker (`public/share-target-handler.js`, audio only, keeps its own copy of
 * these names) and by the Capacitor app (`utils/nativeShare.ts`). The page reads
 * the entry of `?kind=` back and deletes it.
 */
export const SHARE_CACHE = "resona-share-target-v1";

export type ShareKind = "audio" | "document";

export const SHARE_STASH_KEYS: Record<ShareKind, string> = {
	audio: "/share-target/audio",
	document: "/share-target/document",
};

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

/** Puts one shared file where the share-target page looks for `kind`, replacing an older one. */
export async function stashShare(kind: ShareKind, file: Blob, filename: string, title: string): Promise<void> {
	const cache = await caches.open(SHARE_CACHE);
	const headers = new Headers();
	headers.set("content-type", file.type || "application/octet-stream");
	headers.set("x-share-filename", encodeURIComponent(filename));
	headers.set("x-share-title", encodeURIComponent(title));
	await cache.put(SHARE_STASH_KEYS[kind], new Response(file, { headers }));
}
