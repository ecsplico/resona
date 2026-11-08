import { describe, it, expect, vi } from "vitest";
import {
	audioExtension,
	cleanDuration,
	fileExtension,
	normalizeAudio,
	probeDuration,
	resolveAudioMime,
} from "~/utils/audioFile";

describe("fileExtension", () => {
	it("returns the lower-cased extension without the dot", () => {
		expect(fileExtension("Sprachmemo.M4A")).toBe("m4a");
		expect(fileExtension("a.b.mp3")).toBe("mp3");
	});

	it("returns an empty string without an extension", () => {
		expect(fileExtension("memo")).toBe("");
		expect(fileExtension("")).toBe("");
	});
});

describe("resolveAudioMime", () => {
	it("derives the type from the extension when the reported one is empty or generic", () => {
		expect(resolveAudioMime("", "memo.m4a")).toBe("audio/mp4");
		expect(resolveAudioMime("application/octet-stream", "memo.mp3")).toBe("audio/mpeg");
		expect(resolveAudioMime("", "clip.aac")).toBe("audio/aac");
		expect(resolveAudioMime("", "clip.WAV")).toBe("audio/wav");
		expect(resolveAudioMime("", "clip.opus")).toBe("audio/ogg");
	});

	it("canonicalises vendor spellings of the same format", () => {
		expect(resolveAudioMime("audio/x-m4a", "memo.m4a")).toBe("audio/mp4");
		expect(resolveAudioMime("audio/mp4a-latm", "")).toBe("audio/mp4");
		expect(resolveAudioMime("audio/mp3", "")).toBe("audio/mpeg");
		expect(resolveAudioMime("audio/x-wav", "")).toBe("audio/wav");
	});

	it("keeps a specific reported type, including codec parameters", () => {
		expect(resolveAudioMime("audio/webm;codecs=opus", "x.bin")).toBe("audio/webm;codecs=opus");
		expect(resolveAudioMime("audio/mpeg", "memo.m4a")).toBe("audio/mpeg");
	});

	it("leaves an unknown type as reported", () => {
		expect(resolveAudioMime("", "memo.xyz")).toBe("");
		expect(resolveAudioMime("application/octet-stream", "memo")).toBe("application/octet-stream");
	});
});

describe("audioExtension", () => {
	it("maps the MIME type to its extension, ignoring codec parameters", () => {
		expect(audioExtension("audio/mp4")).toBe("m4a");
		expect(audioExtension("audio/mpeg")).toBe("mp3");
		expect(audioExtension("audio/webm;codecs=opus")).toBe("webm");
		expect(audioExtension("audio/x-m4a")).toBe("m4a");
	});

	it("falls back to a known extension of the filename, else webm", () => {
		expect(audioExtension("", "memo.flac")).toBe("flac");
		expect(audioExtension("", "memo.xyz")).toBe("webm");
		expect(audioExtension("")).toBe("webm");
	});
});

describe("normalizeAudio", () => {
	it("keeps the blob and gives the .webm name when the type is already right", () => {
		const blob = new Blob(["a"], { type: "audio/webm" });
		const out = normalizeAudio(blob, "Visite");
		expect(out.blob).toBe(blob);
		expect(out.filename).toBe("Visite.webm");
	});

	it("retypes an untyped m4a upload and names it .m4a (was: untyped blob stored as .webm)", () => {
		const file = new File(["audio"], "Memo.m4a", { type: "" });
		const out = normalizeAudio(file, "Memo");
		expect(out.blob).not.toBe(file);
		expect(out.blob.type).toBe("audio/mp4");
		expect(out.blob.size).toBe(file.size);
		expect(out.filename).toBe("Memo.m4a");
	});

	it("uses an explicit source name when the blob has none (share target)", () => {
		const blob = new Blob(["audio"], { type: "application/octet-stream" });
		const out = normalizeAudio(blob, "Gespräch", "WhatsApp Audio.mp3");
		expect(out.blob.type).toBe("audio/mpeg");
		expect(out.filename).toBe("Gespräch.mp3");
	});

	it("fixes a mismatched extension: Safari's mp4 recording is not stored as .webm", () => {
		const out = normalizeAudio(new Blob(["a"], { type: "audio/mp4" }), "Diktat");
		expect(out.filename).toBe("Diktat.m4a");
	});

	it("falls back to 'recording' for an empty title", () => {
		expect(normalizeAudio(new Blob(["a"], { type: "audio/webm" }), "  ").filename).toBe("recording.webm");
	});
});

describe("cleanDuration", () => {
	it("accepts finite positive numbers and rounds to milliseconds", () => {
		expect(cleanDuration(12.34567)).toBe(12.346);
		expect(cleanDuration(5)).toBe(5);
	});

	it("maps NaN, Infinity, zero, negatives and missing values to 0", () => {
		for (const v of [Number.NaN, Infinity, -Infinity, 0, -3, null, undefined]) {
			expect(cleanDuration(v)).toBe(0);
		}
	});
});

describe("probeDuration", () => {
	const blob = new Blob(["x"], { type: "audio/mp4" });

	it("uses the metadata duration when available", async () => {
		const metadata = vi.fn(async () => 83.5);
		const decode = vi.fn(async () => 99);
		expect(await probeDuration(blob, { metadata, decode })).toBe(83.5);
		expect(decode).not.toHaveBeenCalled();
	});

	it("falls back to decoding when the metadata is unusable (NaN / Infinity)", async () => {
		const decode = vi.fn(async () => 61.2);
		expect(await probeDuration(blob, { metadata: async () => Number.NaN, decode })).toBe(61.2);
		expect(await probeDuration(blob, { metadata: async () => Infinity, decode })).toBe(61.2);
	});

	it("falls back to decoding when the metadata probe throws", async () => {
		const decode = vi.fn(async () => 7);
		const metadata = vi.fn(async () => {
			throw new Error("no metadata");
		});
		expect(await probeDuration(blob, { metadata, decode })).toBe(7);
	});

	it("returns 0 when neither probe yields a duration", async () => {
		const fail = async (): Promise<number> => {
			throw new Error("cannot decode");
		};
		expect(await probeDuration(blob, { metadata: async () => Number.NaN, decode: fail })).toBe(0);
	});
});
