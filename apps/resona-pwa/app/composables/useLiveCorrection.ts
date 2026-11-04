import { computed, getCurrentScope, onScopeDispose, ref, type ComputedRef, type Ref } from "vue";
import type { PcmCapture } from "~/composables/usePcmCapture";
import { pcmToWav } from "~/utils/pcm";

export type LiveBlockState = "open" | "queued" | "correcting" | "corrected" | "failed";

export interface LiveBlock {
	id: number;
	liveText: string;
	correctedText: string | null;
	state: LiveBlockState;
	samples: Int16Array | null;
}

export interface LiveCorrectionStartOptions {
	capture: PcmCapture;
	language: string;
	engine: string;
	chunkSeconds: number;
}

export interface LiveCorrection {
	blocks: Ref<LiveBlock[]>;
	text: ComputedRef<string>;
	pending: ComputedRef<number>;
	start(opts: LiveCorrectionStartOptions): void;
	addFinal(text: string): void;
	finish(): Promise<void>;
	reset(): void;
}

const SAMPLE_RATE = 16000;
const MIN_SAMPLES = SAMPLE_RATE / 2;
const FINISH_TIMEOUT_MS = 90_000;

/**
 * Re-transcribes live audio in closed chunks with a better hub model,
 * one request at a time, replacing each chunk's live text when it succeeds.
 */
export function useLiveCorrection(): LiveCorrection {
	const hub = useHub();
	const blocks = ref<LiveBlock[]>([]);

	let nextId = 1;
	let generation = 0;
	let unsubscribe: (() => void) | null = null;
	let opts: LiveCorrectionStartOptions | null = null;
	let openChunks: Int16Array[] = [];
	let openSamples = 0;
	let running = false;
	let drainWaiters: Array<() => void> = [];

	const text = computed(() =>
		blocks.value
			.map(b => (b.correctedText ?? b.liveText).trim())
			.filter(t => t.length > 0)
			.join(" ")
			.trim(),
	);
	const pending = computed(
		() => blocks.value.filter(b => b.state === "queued" || b.state === "correcting").length,
	);

	function openBlock(): LiveBlock | undefined {
		const last = blocks.value[blocks.value.length - 1];
		return last?.state === "open" ? last : undefined;
	}

	function newBlock(): void {
		blocks.value.push({ id: nextId++, liveText: "", correctedText: null, state: "open", samples: null });
		openChunks = [];
		openSamples = 0;
	}

	function unsubscribeCapture(): void {
		unsubscribe?.();
		unsubscribe = null;
	}

	function concatOpen(): Int16Array {
		const out = new Int16Array(openSamples);
		let offset = 0;
		for (const chunk of openChunks) {
			out.set(chunk, offset);
			offset += chunk.length;
		}
		return out;
	}

	/** Closes the open block; with `dropIfEmpty` a block without text and audio is removed. */
	function closeOpen(dropIfEmpty: boolean): void {
		const block = openBlock();
		if (!block) return;
		if (dropIfEmpty && openSamples === 0 && block.liveText.trim() === "") {
			blocks.value.pop();
			openChunks = [];
			openSamples = 0;
			return;
		}
		if (openSamples < MIN_SAMPLES) {
			block.state = "failed";
			block.samples = null;
		}
		else {
			block.samples = concatOpen();
			block.state = "queued";
		}
		openChunks = [];
		openSamples = 0;
		void pump();
	}

	function settleDrain(): void {
		const waiters = drainWaiters;
		drainWaiters = [];
		for (const w of waiters) w();
	}

	async function pump(): Promise<void> {
		if (running) return;
		running = true;
		const gen = generation;
		try {
			for (;;) {
				if (gen !== generation) return;
				const block = blocks.value.find(b => b.state === "queued");
				if (!block) break;
				block.state = "correcting";
				const samples = block.samples ?? new Int16Array(0);
				let corrected: string | null = null;
				try {
					const result = await hub.transcribeSync(pcmToWav(samples, SAMPLE_RATE), "chunk.wav", {
						language: opts?.language ?? "",
						engine: opts?.engine || undefined,
					});
					const trimmed = (result ?? "").trim();
					corrected = trimmed.length > 0 ? trimmed : null;
				}
				catch {
					corrected = null;
				}
				if (gen !== generation) return;
				block.samples = null;
				if (corrected !== null) {
					block.correctedText = corrected;
					block.state = "corrected";
				}
				else {
					block.state = "failed";
				}
			}
		}
		finally {
			if (gen === generation) {
				running = false;
				settleDrain();
			}
		}
	}

	function start(o: LiveCorrectionStartOptions): void {
		reset();
		opts = o;
		newBlock();
		const gen = generation;
		unsubscribe = o.capture.onFrame((pcm) => {
			if (gen !== generation || !openBlock()) return;
			openChunks.push(pcm);
			openSamples += pcm.length;
		});
	}

	function addFinal(t: string): void {
		const block = openBlock();
		if (!block || !opts) return;
		const piece = t.trim();
		if (piece) block.liveText = block.liveText ? `${block.liveText} ${piece}` : piece;
		if (openSamples >= opts.chunkSeconds * SAMPLE_RATE) {
			closeOpen(false);
			newBlock();
		}
	}

	function finish(): Promise<void> {
		unsubscribeCapture();
		closeOpen(true);
		if (!running && !blocks.value.some(b => b.state === "queued" || b.state === "correcting")) {
			return Promise.resolve();
		}
		return new Promise((resolve) => {
			const timer = setTimeout(done, FINISH_TIMEOUT_MS);
			function done(): void {
				clearTimeout(timer);
				resolve();
			}
			drainWaiters.push(done);
		});
	}

	function reset(): void {
		unsubscribeCapture();
		generation += 1;
		running = false;
		settleDrain();
		opts = null;
		openChunks = [];
		openSamples = 0;
		blocks.value = [];
	}

	if (getCurrentScope()) onScopeDispose(reset);

	return { blocks, text, pending, start, addFinal, finish, reset };
}
