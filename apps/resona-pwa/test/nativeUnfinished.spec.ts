import "fake-indexeddb/auto";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { claimUnfinished, discardUnfinished, listUnfinished, loadUnfinished, releaseUnfinished } from "~/data/unfinishedRecordings";
import { scanAdts } from "~/utils/adts";

const native = vi.hoisted(() => ({
	platform: "android",
	status: "INACTIVE",
	files: [] as Array<{ name: string; type: "file" | "directory"; size: number; mtime: number; ctime?: number; uri: string }>,
	bytes: new Map<string, Uint8Array>(),
}));

const { recorder, filesystem } = vi.hoisted(() => ({
	recorder: { getRecordingStatus: vi.fn(async () => ({ status: native.status })) },
	filesystem: {
		readdir: vi.fn(async (_opts: { path: string; directory: string }) => ({ files: native.files })),
		stat: vi.fn(async ({ path }: { path: string }) => {
			const file = native.files.find(f => `capacitor-audio-recorder/${f.name}` === path);
			if (!file) throw new Error("File does not exist");
			return file;
		}),
		deleteFile: vi.fn(async (_opts: { path: string; directory: string }) => undefined),
	},
}));

vi.mock("@capacitor/core", () => ({
	Capacitor: {
		isNativePlatform: () => true,
		isPluginAvailable: () => true,
		getPlatform: () => native.platform,
		convertFileSrc: (uri: string) => uri.replace("file://", "https://localhost/_capacitor_file_"),
	},
}));
vi.mock("@capgo/capacitor-audio-recorder", () => ({ CapacitorAudioRecorder: recorder }));
vi.mock("@capacitor/filesystem", () => ({ Filesystem: filesystem, Directory: { Cache: "CACHE" } }));

/** One ADTS frame (AAC LC, 44.1 kHz, mono) of `length` bytes, 7-byte header without CRC. */
function frame(length: number): Uint8Array {
	const f = new Uint8Array(length);
	f.set([0xFF, 0xF1, (1 << 6) | (4 << 2), (1 << 6) | (length >> 11), (length >> 3) & 0xFF, ((length & 7) << 5) | 0x1F, 0xFC]);
	return f;
}

function concat(...parts: Uint8Array[]): Uint8Array {
	const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
	let at = 0;
	for (const p of parts) {
		out.set(p, at);
		at += p.length;
	}
	return out;
}

// Written by an app process that is gone: long before this web view started.
const OLD = 1_000_000_000_000;

function addFile(name: string, bytes: Uint8Array, mtime = OLD, type: "file" | "directory" = "file"): void {
	const uri = `file:///data/cache/capacitor-audio-recorder/${name}`;
	native.files.push({ name, type, size: bytes.length, mtime, uri });
	native.bytes.set(uri.replace("file://", "https://localhost/_capacitor_file_"), bytes);
}

describe("native recorder files left by a killed app", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		native.platform = "android";
		native.status = "INACTIVE";
		native.files = [];
		native.bytes.clear();
		vi.stubGlobal("fetch", vi.fn(async (url: string) => {
			const bytes = native.bytes.get(url);
			return bytes ? new Response(bytes) : new Response(null, { status: 404 });
		}));
	});

	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it("offers only .aac recordings written before this web view, oldest first", async () => {
		addFile("recording-20261111-100000-b.aac", frame(100));
		addFile("recording-20261111-090000-a.aac", frame(100));
		addFile("recording-20261111-080000-old.m4a", frame(100));
		addFile("recording-20261111-110000-x.aac", new Uint8Array(0), OLD, "directory");
		addFile("recording-20261111-120000-now.aac", frame(100), Date.now() + 60_000);
		addFile("notes.txt", frame(100));

		const offered = await listUnfinished();

		expect(filesystem.readdir).toHaveBeenCalledWith({ path: "capacitor-audio-recorder", directory: "CACHE" });
		expect(offered.map(m => m.id)).toEqual([
			"native:recording-20261111-090000-a.aac",
			"native:recording-20261111-100000-b.aac",
		]);
		expect(offered[0]).toMatchObject({ kind: "record", label: null, mime: "audio/aac" });
		expect(new Date(offered[0]!.startedAt).getHours()).toBe(9);
		expect(filesystem.deleteFile).not.toHaveBeenCalled();
	});

	it("deletes empty files instead of offering them", async () => {
		addFile("recording-20261111-090000-a.aac", new Uint8Array(0));

		expect(await listUnfinished()).toEqual([]);
		expect(filesystem.deleteFile).toHaveBeenCalledWith({ path: "capacitor-audio-recorder/recording-20261111-090000-a.aac", directory: "CACHE" });
	});

	it("offers nothing while the plugin records", async () => {
		native.status = "RECORDING";
		addFile("recording-20261111-090000-a.aac", frame(100));

		expect(await listUnfinished()).toEqual([]);
		expect(filesystem.readdir).not.toHaveBeenCalled();
	});

	it("offers nothing outside the Android app or without a recorder directory", async () => {
		addFile("recording-20261111-090000-a.aac", frame(100));
		native.platform = "ios";
		expect(await listUnfinished()).toEqual([]);
		expect(filesystem.readdir).not.toHaveBeenCalled();

		native.platform = "android";
		filesystem.readdir.mockRejectedValueOnce(new Error("Directory does not exist"));
		expect(await listUnfinished()).toEqual([]);
	});

	it("loads the whole frames of a file cut off mid-frame, with their length", async () => {
		const name = "recording-20261111-090000-a.aac";
		const frames = Array.from({ length: 43 }, () => frame(200));
		// The kill left half a frame at the end.
		addFile(name, concat(...frames, frame(200).subarray(0, 90)));

		const recovered = await loadUnfinished(`native:${name}`);

		expect(recovered!.blob.type).toBe("audio/aac");
		expect(recovered!.blob.size).toBe(43 * 200);
		// 43 frames × 1024 samples at 44.1 kHz ≈ 1 s.
		expect(recovered!.seconds).toBe(1);
		expect(recovered!.meta.id).toBe(`native:${name}`);
	});

	it("loads nothing from a file without one whole frame", async () => {
		const name = "recording-20261111-090000-a.aac";
		addFile(name, frame(200).subarray(0, 50));

		expect(await loadUnfinished(`native:${name}`)).toBeNull();
	});

	it("deletes the file once it is saved or discarded, and never offers a held one", async () => {
		const name = "recording-20261111-090000-a.aac";
		addFile(name, frame(100));

		claimUnfinished(`native:${name}`);
		expect(await listUnfinished()).toEqual([]);
		releaseUnfinished(`native:${name}`);
		expect(await listUnfinished()).toHaveLength(1);

		await discardUnfinished(`native:${name}`);
		expect(filesystem.deleteFile).toHaveBeenCalledWith({ path: `capacitor-audio-recorder/${name}`, directory: "CACHE" });
	});

	it("touches no file outside the recorder's names", async () => {
		await discardUnfinished("native:../files/other.aac");
		expect(await loadUnfinished("native:../files/other.aac")).toBeNull();
		expect(filesystem.deleteFile).not.toHaveBeenCalled();
	});
});

describe("scanAdts", () => {
	it("stops at the first byte that is no frame header", () => {
		const scan = scanAdts(concat(frame(100), frame(100), new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8])));
		expect(scan).toMatchObject({ length: 200, frames: 2 });
	});

	it("does not take MP3 frames for ADTS", () => {
		// MPEG-1 Layer III frame sync: 0xFFFB.
		expect(scanAdts(new Uint8Array([0xFF, 0xFB, 0x90, 0x64, 0, 0, 0, 0, 0])).frames).toBe(0);
	});
});
