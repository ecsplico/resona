import { Capacitor } from "@capacitor/core";
import { CapacitorAudioRecorder } from "@capgo/capacitor-audio-recorder";
import { Directory, Filesystem } from "@capacitor/filesystem";
import type { FileInfo } from "@capacitor/filesystem";
import { scanAdts } from "~/utils/adts";
import type { RecoveredAudio, UnfinishedRecording } from "./unfinishedRecordings";

/**
 * Recordings the native recorder (Android app) left in its cache directory: the
 * plugin writes `<cache>/capacitor-audio-recorder/recording-<yyyyMMdd-HHmmss>-<uuid>.aac`
 * and `useNativeRecorder().stop()` hands it to its caller as the session
 * (`nativeSessionId`), which deletes it once the audio is saved or discarded. So a
 * file there that this web view did not write belongs to a recording whose app was
 * killed (while recording, or before the save sheet saved it). ADTS stays readable
 * up to its last whole frame, so it can be offered like
 * an IndexedDB session (`unfinishedRecordings.ts` merges both).
 *
 * Not offered: files written since this web view started (`performance.timeOrigin`;
 * the recording in progress, or one `stop()` is still reading), anything while the
 * plugin reports a recording, `.m4a` files (without their index, written at stop,
 * they are unreadable). Empty files are deleted.
 */

export const NATIVE_UNFINISHED_PREFIX = "native:";
export const NATIVE_RECORDER_DIR = "capacitor-audio-recorder";
const MIME = "audio/aac";
const NAME = /^recording-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})(?:-[\w-]+)?\.aac$/i;

/** When this web view started: the recorder files it wrote are newer. */
const contextStart = typeof performance !== "undefined" && performance.timeOrigin > 0 ? performance.timeOrigin : Date.now();

export function isNativeUnfinished(id: string): boolean {
	return id.startsWith(NATIVE_UNFINISHED_PREFIX);
}

function available(): boolean {
	return Capacitor.isNativePlatform()
		&& Capacitor.getPlatform() === "android"
		&& Capacitor.isPluginAvailable("CapacitorAudioRecorder");
}

/** The file name behind `id`, or null for anything that is not one of the recorder's files. */
function fileName(id: string): string | null {
	const name = id.slice(NATIVE_UNFINISHED_PREFIX.length);
	return isNativeUnfinished(id) && NAME.test(name) ? name : null;
}

/** The session id of a recorder file (`stopRecording().uri`), or null for any other path. */
export function nativeSessionId(uri: string): string | null {
	const match = /\/capacitor-audio-recorder\/([^/]+)$/.exec(uri);
	return match && NAME.test(match[1]!) ? NATIVE_UNFINISHED_PREFIX + match[1] : null;
}

function filePath(name: string): string {
	return `${NATIVE_RECORDER_DIR}/${name}`;
}

/** Start time from the name (device local time), else the file's own times. */
function startedAt(name: string, file: Pick<FileInfo, "ctime" | "mtime">): number {
	const m = NAME.exec(name);
	if (m) {
		const [y, mo, d, h, mi, s] = m.slice(1, 7).map(Number) as [number, number, number, number, number, number];
		const at = new Date(y, mo - 1, d, h, mi, s).getTime();
		if (!Number.isNaN(at)) return at;
	}
	return file.ctime ?? file.mtime;
}

function toMeta(name: string, file: Pick<FileInfo, "ctime" | "mtime">): UnfinishedRecording {
	const start = startedAt(name, file);
	return {
		id: NATIVE_UNFINISHED_PREFIX + name,
		kind: "record",
		label: null,
		mime: MIME,
		startedAt: new Date(start).toISOString(),
		updatedAt: new Date(file.mtime).toISOString(),
		// Wall clock from start to the last write, pauses included; `load` measures the audio.
		elapsed: Math.max(0, Math.round((file.mtime - start) / 1000)),
	};
}

/** Recorder files a killed app left behind; empty outside the Android app. */
export async function listNativeUnfinished(): Promise<UnfinishedRecording[]> {
	if (!available()) return [];
	try {
		// A recording runs (this web view's or one it lost after a reload): offer nothing now.
		if ((await CapacitorAudioRecorder.getRecordingStatus()).status !== "INACTIVE") return [];
		const { files } = await Filesystem.readdir({ path: NATIVE_RECORDER_DIR, directory: Directory.Cache });
		const left: UnfinishedRecording[] = [];
		for (const file of files) {
			if (file.type !== "file" || !NAME.test(file.name) || file.mtime >= contextStart) continue;
			if (file.size === 0) {
				await Filesystem.deleteFile({ path: filePath(file.name), directory: Directory.Cache }).catch(() => undefined);
				continue;
			}
			left.push(toMeta(file.name, file));
		}
		return left;
	}
	catch {
		// No recorder directory yet, or the plugin cannot tell: nothing to offer.
		return [];
	}
}

/** The file's whole ADTS frames; null when it holds none. */
export async function loadNativeUnfinished(id: string): Promise<RecoveredAudio | null> {
	const name = fileName(id);
	if (!name || !available()) return null;
	const path = filePath(name);
	const stat = await Filesystem.stat({ path, directory: Directory.Cache });
	const res = await fetch(Capacitor.convertFileSrc(stat.uri));
	if (!res.ok) throw new Error(`Aufnahme nicht lesbar (${res.status}).`);
	const bytes = new Uint8Array(await res.arrayBuffer());
	const scan = scanAdts(bytes);
	if (scan.frames === 0) return null;
	return {
		meta: toMeta(name, stat),
		blob: new Blob([bytes.subarray(0, scan.length)], { type: MIME }),
		seconds: Math.round(scan.seconds),
	};
}

export async function discardNativeUnfinished(id: string): Promise<void> {
	const name = fileName(id);
	if (!name || !available()) return;
	await Filesystem.deleteFile({ path: filePath(name), directory: Directory.Cache }).catch(() => undefined);
}
