import { get, set, del, keys, clear, createStore } from "idb-keyval";
import type { UseStore } from "idb-keyval";
import { onScopeDispose, ref } from "vue";
import type { Ref } from "vue";

export interface QueueInput {
	blob: Blob;
	title: string;
	language: string;
	profile: string;
	transcribe?: boolean;
	duration_seconds?: number;
}

export interface QueuedItem extends QueueInput {
	id: string;
	createdAt: number;
}

// Stored shape: the blob is persisted as raw bytes + type so it survives
// structured cloning into IndexedDB reliably across environments. The Blob is
// rehydrated on read.
interface StoredItem extends Omit<QueuedItem, "blob"> {
	bytes: ArrayBuffer;
	blobType: string;
	seq: number;
}

export interface SyncResult {
	succeeded: string[];
	failed: string[];
}

let store: UseStore | null = null;

function getStore(): UseStore {
	if (!store) {
		store = createStore("resona-offline-queue", "items");
	}
	return store;
}

function newId(): string {
	const c = typeof globalThis !== "undefined" ? globalThis.crypto : undefined;
	if (c && typeof c.randomUUID === "function") {
		return c.randomUUID();
	}
	return `q-${performance.now().toString(36)}-${counter++}`;
}

let counter = 0;
let seqCounter = 0;

export function useOfflineQueue() {
	const s = getStore();
	const pendingCount: Ref<number> = ref(0);

	async function pending(): Promise<QueuedItem[]> {
		const ids = await keys(s);
		const stored: StoredItem[] = [];
		for (const key of ids) {
			const item = await get<StoredItem>(key as IDBValidKey, s);
			if (item) {
				stored.push(item);
			}
		}
		// seq is a monotonic insertion counter — a stable tiebreaker when two
		// items share a createdAt millisecond.
		stored.sort((a, b) => a.createdAt - b.createdAt || a.seq - b.seq);
		const items = stored.map(({ bytes, blobType, seq: _seq, ...rest }) => ({
			...rest,
			blob: new Blob([bytes], { type: blobType }),
		}));
		pendingCount.value = items.length;
		return items;
	}

	async function enqueue(input: QueueInput): Promise<string> {
		const id = newId();
		const { blob, ...rest } = input;
		const stored: StoredItem = {
			...rest,
			id,
			createdAt: Date.now(),
			bytes: await blob.arrayBuffer(),
			blobType: blob.type,
			seq: seqCounter++,
		};
		await set(id, stored, s);
		pendingCount.value += 1;
		return id;
	}

	async function remove(id: string): Promise<void> {
		await del(id, s);
		if (pendingCount.value > 0) {
			pendingCount.value -= 1;
		}
	}

	async function sync(uploadFn: (item: QueuedItem) => Promise<unknown>): Promise<SyncResult> {
		const items = await pending();
		const succeeded: string[] = [];
		const failed: string[] = [];
		for (const item of items) {
			try {
				await uploadFn(item);
				await remove(item.id);
				succeeded.push(item.id);
			}
			catch {
				failed.push(item.id);
			}
		}
		pendingCount.value = failed.length;
		return { succeeded, failed };
	}

	function startAutoSync(uploadFn: (item: QueuedItem) => Promise<unknown>): () => void {
		if (typeof window === "undefined" || typeof navigator === "undefined") {
			return () => {};
		}
		const handler = (): void => {
			if (navigator.onLine) {
				void sync(uploadFn);
			}
		};
		window.addEventListener("online", handler);
		if (navigator.onLine) {
			void sync(uploadFn);
		}
		const stop = (): void => window.removeEventListener("online", handler);
		onScopeDispose(stop);
		return stop;
	}

	async function clearAll(): Promise<void> {
		await clear(s);
		pendingCount.value = 0;
	}

	return {
		pendingCount,
		pending,
		enqueue,
		remove,
		sync,
		startAutoSync,
		clearAll,
	};
}
