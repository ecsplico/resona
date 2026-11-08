/**
 * Helpers for audio that did not come from our own recorder (uploads, shares):
 * a trustworthy MIME type + file extension, and the real duration.
 *
 * Why: phones hand over m4a/aac/mp3 files with an empty or generic type
 * (`""`, `application/octet-stream`, `audio/x-m4a`), and the file used to be
 * stored as `<title>.webm` with whatever type the browser reported. Directus
 * serves `directus_files.type` as the asset Content-Type, so the player got a
 * wrong/unknown type and refused to play it.
 */

/** Canonical audio MIME type per file extension (lower case, no dot). */
const MIME_BY_EXTENSION: Record<string, string> = {
	"m4a": "audio/mp4",
	"mp4": "audio/mp4",
	"aac": "audio/aac",
	"mp3": "audio/mpeg",
	"mpga": "audio/mpeg",
	"wav": "audio/wav",
	"wave": "audio/wav",
	"ogg": "audio/ogg",
	"oga": "audio/ogg",
	"opus": "audio/ogg",
	"webm": "audio/webm",
	"weba": "audio/webm",
	"flac": "audio/flac",
	"3gp": "audio/3gpp",
	"amr": "audio/amr",
	"caf": "audio/x-caf",
};

/** Preferred extension per canonical MIME type. */
const EXTENSION_BY_MIME: Record<string, string> = {
	"audio/mp4": "m4a",
	"audio/aac": "aac",
	"audio/mpeg": "mp3",
	"audio/wav": "wav",
	"audio/ogg": "ogg",
	"audio/webm": "webm",
	"audio/flac": "flac",
	"audio/3gpp": "3gp",
	"audio/amr": "amr",
	"audio/x-caf": "caf",
	"video/webm": "webm",
	"video/mp4": "mp4",
};

/** Vendor / legacy spellings → the type browsers actually play. */
const MIME_ALIASES: Record<string, string> = {
	"audio/x-m4a": "audio/mp4",
	"audio/m4a": "audio/mp4",
	"audio/mp4a-latm": "audio/mp4",
	"audio/x-mp4": "audio/mp4",
	"audio/mp3": "audio/mpeg",
	"audio/x-mp3": "audio/mpeg",
	"audio/mpeg3": "audio/mpeg",
	"audio/x-mpeg-3": "audio/mpeg",
	"audio/x-wav": "audio/wav",
	"audio/wave": "audio/wav",
	"audio/vnd.wave": "audio/wav",
	"audio/x-aac": "audio/aac",
	"audio/aacp": "audio/aac",
	"audio/x-flac": "audio/flac",
	"audio/x-ogg": "audio/ogg",
};

/** Types that say nothing about the content. */
const GENERIC_TYPES = new Set(["", "application/octet-stream", "binary/octet-stream", "application/x-unknown", "application/unknown"]);

/** Lower-case extension of `name` without the dot, or "" when it has none. */
export function fileExtension(name: string): string {
	const match = /\.([A-Za-z0-9]+)$/.exec(name.trim());
	return match ? match[1]!.toLowerCase() : "";
}

/** `audio/mp4;codecs=mp4a.40.2` → `audio/mp4`, aliases resolved. */
function baseType(type: string): string {
	const base = type.split(";")[0]!.trim().toLowerCase();
	return MIME_ALIASES[base] ?? base;
}

/**
 * The MIME type the stored audio should carry. A specific audio/video type
 * reported by the browser wins (codec parameters kept, aliases canonicalised);
 * a missing or generic one is derived from the file extension. Unknown stays
 * as reported.
 */
export function resolveAudioMime(reportedType: string, name = ""): string {
	const reported = reportedType.trim().toLowerCase();
	const base = baseType(reported);
	if (GENERIC_TYPES.has(base)) {
		return MIME_BY_EXTENSION[fileExtension(name)] ?? reported;
	}
	// Already canonical: keep codec parameters untouched.
	return base === reported.split(";")[0]!.trim() ? reported : base;
}

/** File extension (no dot) that matches `mime`, else the name's own, else `webm`. */
export function audioExtension(mime: string, name = ""): string {
	return EXTENSION_BY_MIME[baseType(mime)] ?? (MIME_BY_EXTENSION[fileExtension(name)] ? fileExtension(name) : "webm");
}

export interface NormalizedAudio {
	blob: Blob;
	/** `<base>.<ext>`, the extension matching `blob.type`. */
	filename: string;
}

/**
 * Gives audio a correct MIME type and a matching filename. Returns the very
 * same blob when its type is already right; otherwise a retyped copy.
 * `sourceName` is the original filename (a `File`'s name is used when omitted).
 */
export function normalizeAudio(blob: Blob, base: string, sourceName?: string): NormalizedAudio {
	const original = sourceName ?? (blob instanceof File ? blob.name : "");
	const type = resolveAudioMime(blob.type, original);
	const retyped = type === blob.type ? blob : new Blob([blob], { type });
	const name = base.trim() || "recording";
	return { blob: retyped, filename: `${name}.${audioExtension(type, original)}` };
}

/** A usable duration (finite, > 0) rounded to milliseconds, else 0. */
export function cleanDuration(seconds: number | null | undefined): number {
	if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) {
		return 0;
	}
	return Math.round(seconds * 1000) / 1000;
}

export interface DurationProbes {
	/** Duration via the media element's metadata (may be NaN/Infinity/throw). */
	metadata: (blob: Blob) => Promise<number>;
	/** Duration via full decoding (may throw). */
	decode: (blob: Blob) => Promise<number>;
}

const METADATA_TIMEOUT_MS = 5000;

/** Reads the duration from a media element's metadata; resolves NaN when unknown. */
export function metadataDuration(blob: Blob, timeoutMs = METADATA_TIMEOUT_MS): Promise<number> {
	return new Promise<number>((resolve) => {
		if (typeof document === "undefined" || typeof URL === "undefined" || typeof URL.createObjectURL !== "function") {
			resolve(Number.NaN);
			return;
		}
		const url = URL.createObjectURL(blob);
		const audio = document.createElement("audio");
		audio.preload = "metadata";
		let settled = false;
		const finish = (value: number): void => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			audio.removeAttribute("src");
			audio.load();
			URL.revokeObjectURL(url);
			resolve(value);
		};
		const timer = setTimeout(() => finish(Number.NaN), timeoutMs);
		audio.onerror = () => finish(Number.NaN);
		audio.onloadedmetadata = () => {
			if (audio.duration === Infinity) {
				// MediaRecorder WebM carries no length: seeking far past the end
				// makes the browser compute it and fire durationchange.
				audio.ontimeupdate = () => finish(audio.duration);
				audio.currentTime = 1e101;
				return;
			}
			finish(audio.duration);
		};
		audio.src = url;
	});
}

/** Reads the duration by decoding the whole file with Web Audio. */
export async function decodedDuration(blob: Blob): Promise<number> {
	const g = globalThis as Record<string, unknown>;
	const Ctor = (g.OfflineAudioContext ?? g.webkitOfflineAudioContext) as typeof OfflineAudioContext | undefined;
	if (!Ctor) {
		throw new Error("Web Audio nicht verfügbar");
	}
	const buffer = await new Ctor(1, 1, 16000).decodeAudioData(await blob.arrayBuffer());
	return buffer.duration;
}

const BROWSER_PROBES: DurationProbes = { metadata: blob => metadataDuration(blob), decode: decodedDuration };

/**
 * The real length of `blob` in seconds: media-element metadata first, full
 * decoding as fallback. 0 when neither works (the audio is then still stored).
 */
export async function probeDuration(blob: Blob, probes: DurationProbes = BROWSER_PROBES): Promise<number> {
	for (const probe of [probes.metadata, probes.decode]) {
		try {
			const seconds = cleanDuration(await probe(blob));
			if (seconds > 0) {
				return seconds;
			}
		}
		catch {
			// try the next probe
		}
	}
	return 0;
}
