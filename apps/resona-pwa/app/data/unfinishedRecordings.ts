import { createStore, del, delMany, get, getMany, keys, promisifyRequest, set, values } from "idb-keyval";
import type { UseStore } from "idb-keyval";
import { mergeToWav } from "~/utils/audioMerge";
import { discardNativeUnfinished, isNativeUnfinished, listNativeUnfinished, loadNativeUnfinished } from "./nativeUnfinished";

/**
 * Audio of recordings in progress, kept in this browser's IndexedDB (both data
 * modes) while they are recorded: `useRecorder` appends every timeslice chunk,
 * so a killed or crashed page loses at most the last timeslice. A session stays
 * until its owner discards it (after the audio reached its real home: saved
 * recording, appended part, explicit Verwerfen); one left behind is offered on
 * the next open of the recordings list. In the Android app the native recorder's
 * files a killed app left behind are offered the same way (`nativeUnfinished.ts`,
 * ids `native:<file name>`).
 *
 * A session "owned" by a live page is never offered: same tab via `owned`,
 * other tabs via a Web Lock named after the session (released by the browser
 * when that page goes away).
 */

export type UnfinishedKind = "record" | "live" | "redictation";

export interface UnfinishedRecording {
	id: string;
	kind: UnfinishedKind;
	/** Title to suggest when it is saved (e.g. the live preset), else null. */
	label: string | null;
	mime: string;
	startedAt: string;
	updatedAt: string;
	/** Recorded seconds when the last chunk was written. */
	elapsed: number;
}

export interface RecoveredAudio {
	meta: UnfinishedRecording;
	blob: Blob;
	seconds: number;
}

interface StoredChunk {
	bytes: ArrayBuffer;
	type: string;
}

const LOCK_PREFIX = "resona-unfinished:";

let stores: { meta: UseStore; chunks: UseStore } | null = null;

function db(): { meta: UseStore; chunks: UseStore } {
	stores ??= {
		meta: createStore("resona-unfinished-recordings", "items"),
		chunks: createStore("resona-unfinished-chunks", "items"),
	};
	return stores;
}

/** Chunk keys sort by part, then by order within the part. */
function chunkKey(id: string, part: number, seq: number): string {
	return `${id}/${String(part).padStart(4, "0")}/${String(seq).padStart(8, "0")}`;
}

async function chunkKeysOf(id: string): Promise<string[]> {
	const all = await keys<string>(db().chunks);
	return all.filter(k => typeof k === "string" && k.startsWith(`${id}/`)).sort();
}

const owned = new Map<string, () => void>();

function lockManager(): LockManager | undefined {
	if (typeof navigator === "undefined") return undefined;
	// Typed as always present; missing in older Safari and in happy-dom.
	const locks: LockManager | undefined = navigator.locks;
	return locks;
}

/** Marks a session as in use by this page, so it is not offered for recovery. */
export function claimUnfinished(id: string): void {
	if (owned.has(id)) return;
	let release = () => {};
	const locks = lockManager();
	if (locks?.request) {
		// Executor form instead of Promise.withResolvers: iOS Safari < 17.4.
		const held = new Promise<void>((resolve) => {
			release = resolve;
		});
		void locks.request(LOCK_PREFIX + id, () => held).catch(() => {});
	}
	owned.set(id, release);
}

/** This page no longer holds the session; its data stays for recovery. */
export function releaseUnfinished(id: string): void {
	owned.get(id)?.();
	owned.delete(id);
}

export async function beginUnfinished(meta: UnfinishedRecording): Promise<void> {
	await set(meta.id, meta, db().meta);
}

export async function appendUnfinishedChunk(id: string, part: number, seq: number, chunk: Blob, elapsed: number): Promise<void> {
	const stored: StoredChunk = { bytes: await chunk.arrayBuffer(), type: chunk.type };
	await set(chunkKey(id, part, seq), stored, db().chunks);
	// One readwrite transaction that leaves a discarded session's meta absent (idb-keyval's
	// `update` would always write, recreating it as `undefined`).
	await db().meta("readwrite", (store) => {
		const read = store.get(id);
		read.onsuccess = () => {
			const meta = read.result as UnfinishedRecording | undefined;
			if (meta) store.put({ ...meta, elapsed, updatedAt: new Date().toISOString() }, id);
		};
		return promisifyRequest(store.transaction);
	});
}

/** Sessions nobody records or holds right now, oldest first. Empty without IndexedDB. */
export async function listUnfinished(): Promise<UnfinishedRecording[]> {
	let all: UnfinishedRecording[];
	try {
		all = await values<UnfinishedRecording>(db().meta);
	}
	catch {
		all = [];
	}
	let held = new Set<string>();
	try {
		const state = await lockManager()?.query?.();
		held = new Set((state?.held ?? []).map(lock => lock.name ?? ""));
	}
	catch {
		// No lock information: only this tab's sessions are known to be in use.
	}
	return [...all, ...await listNativeUnfinished()]
		.filter(meta => !owned.has(meta.id) && !held.has(LOCK_PREFIX + meta.id))
		.sort((a, b) => a.startedAt.localeCompare(b.startedAt));
}

/**
 * The session's audio as one blob: a single part as recorded, several parts
 * (resumed after an interruption) merged to one WAV. Null when no chunk was written.
 */
export async function loadUnfinished(id: string): Promise<RecoveredAudio | null> {
	if (isNativeUnfinished(id)) return loadNativeUnfinished(id);
	const meta = await get<UnfinishedRecording>(id, db().meta);
	if (!meta) return null;
	const chunkKeys = await chunkKeysOf(id);
	const chunks = await getMany<StoredChunk | undefined>(chunkKeys, db().chunks);
	const parts = new Map<string, Blob[]>();
	chunkKeys.forEach((key, i) => {
		const chunk = chunks[i];
		if (!chunk) return;
		const part = key.split("/")[1]!;
		const list = parts.get(part) ?? [];
		list.push(new Blob([chunk.bytes], { type: chunk.type || meta.mime }));
		parts.set(part, list);
	});
	const blobs = [...parts.values()].map(list => new Blob(list, { type: meta.mime }));
	if (blobs.length === 0) return null;
	if (blobs.length === 1) return { meta, blob: blobs[0]!, seconds: meta.elapsed };
	try {
		const merged = await mergeToWav(blobs);
		return { meta, blob: merged.blob, seconds: merged.seconds };
	}
	catch {
		// Keeps every byte, as useRecorder.stop() does; players may only play the first part.
		return { meta, blob: new Blob(blobs, { type: meta.mime }), seconds: meta.elapsed };
	}
}

/** Deletes the session and its audio for good. */
export async function discardUnfinished(id: string): Promise<void> {
	releaseUnfinished(id);
	if (isNativeUnfinished(id)) {
		await discardNativeUnfinished(id);
		return;
	}
	try {
		await delMany(await chunkKeysOf(id), db().chunks);
		await del(id, db().meta);
	}
	catch {
		// Without IndexedDB nothing was stored.
	}
}
