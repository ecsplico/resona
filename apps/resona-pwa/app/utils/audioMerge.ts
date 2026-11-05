import { floatTo16kPCM, pcmToWav } from "~/utils/pcm";

const SAMPLE_RATE = 16000;

export interface MergedAudio {
	/** 16 kHz mono PCM16 WAV of all parts in order. */
	blob: Blob;
	/** Length of the merged audio in seconds. */
	seconds: number;
}

function offlineContextCtor(): typeof OfflineAudioContext {
	const g = globalThis as Record<string, unknown>;
	const Ctor = (g.OfflineAudioContext ?? g.webkitOfflineAudioContext) as typeof OfflineAudioContext | undefined;
	if (!Ctor) throw new Error("Dieser Browser kann Audio nicht zusammenführen.");
	return Ctor;
}

/** Decodes one part and renders it to 16 kHz mono (downmix + resample). */
async function renderMono16k(Ctor: typeof OfflineAudioContext, part: Blob): Promise<Float32Array> {
	let decoded: AudioBuffer;
	try {
		decoded = await new Ctor(1, 1, SAMPLE_RATE).decodeAudioData(await part.arrayBuffer());
	}
	catch (err) {
		throw new Error("Audio konnte nicht dekodiert werden.", { cause: err });
	}
	const length = Math.max(1, Math.ceil(decoded.duration * SAMPLE_RATE));
	const ctx = new Ctor(1, length, SAMPLE_RATE);
	const source = ctx.createBufferSource();
	source.buffer = decoded;
	// A one-channel destination mixes multi-channel sources down to mono.
	source.connect(ctx.destination);
	source.start();
	const rendered = await ctx.startRendering();
	return rendered.getChannelData(0);
}

/**
 * Concatenates audio parts (any format the browser decodes) into one 16 kHz
 * mono WAV. Browser only: needs Web Audio's OfflineAudioContext.
 */
export async function mergeToWav(parts: Blob[]): Promise<MergedAudio> {
	const Ctor = offlineContextCtor();
	const encoded: Int16Array[] = [];
	for (const part of parts) {
		encoded.push(floatTo16kPCM(await renderMono16k(Ctor, part), SAMPLE_RATE));
	}
	const total = encoded.reduce((n, samples) => n + samples.length, 0);
	const joined = new Int16Array(total);
	let offset = 0;
	for (const samples of encoded) {
		joined.set(samples, offset);
		offset += samples.length;
	}
	return { blob: pcmToWav(joined, SAMPLE_RATE), seconds: total / SAMPLE_RATE };
}
