/**
 * Downsample a mono Float32 audio buffer to `targetRate` (default 16 kHz) and
 * encode it as little-endian signed 16-bit PCM.
 *
 * Pure function — no browser APIs — so it is unit-testable under happy-dom.
 *
 * Resampling uses linear interpolation. When `inputRate <= targetRate` the
 * samples are encoded as-is (no upsampling).
 */
export function floatTo16kPCM(
	input: Float32Array,
	inputRate: number,
	targetRate = 16000,
): Int16Array {
	const resampled = inputRate > targetRate
		? downsample(input, inputRate, targetRate)
		: input;

	const out = new Int16Array(resampled.length);
	for (let i = 0; i < resampled.length; i += 1) {
		out[i] = encodeSample(resampled[i] ?? 0);
	}
	return out;
}

function downsample(input: Float32Array, inputRate: number, targetRate: number): Float32Array {
	const ratio = inputRate / targetRate;
	const length = Math.floor(input.length * targetRate / inputRate);
	const out = new Float32Array(length);
	for (let i = 0; i < length; i += 1) {
		const pos = i * ratio;
		const left = Math.floor(pos);
		const right = Math.min(left + 1, input.length - 1);
		const frac = pos - left;
		const a = input[left] ?? 0;
		const b = input[right] ?? 0;
		out[i] = a + (b - a) * frac;
	}
	return out;
}

function encodeSample(sample: number): number {
	const s = Math.max(-1, Math.min(1, sample));
	const scaled = s < 0 ? s * 32768 : s * 32767;
	return Math.max(-32768, Math.min(32767, Math.round(scaled)));
}

/**
 * Wrap mono 16-bit PCM samples in a 44-byte RIFF/WAVE header (little-endian).
 */
export function pcmToWav(samples: Int16Array, sampleRate: number): Blob {
	const dataLength = samples.length * 2;
	const buffer = new ArrayBuffer(44 + dataLength);
	const view = new DataView(buffer);
	const writeTag = (offset: number, tag: string) => {
		for (let i = 0; i < 4; i += 1) view.setUint8(offset + i, tag.charCodeAt(i));
	};
	writeTag(0, "RIFF");
	view.setUint32(4, 36 + dataLength, true);
	writeTag(8, "WAVE");
	writeTag(12, "fmt ");
	view.setUint32(16, 16, true); // fmt chunk size
	view.setUint16(20, 1, true); // PCM
	view.setUint16(22, 1, true); // mono
	view.setUint32(24, sampleRate, true);
	view.setUint32(28, sampleRate * 2, true); // byte rate
	view.setUint16(32, 2, true); // block align
	view.setUint16(34, 16, true); // bits per sample
	writeTag(36, "data");
	view.setUint32(40, dataLength, true);
	for (let i = 0; i < samples.length; i += 1) {
		view.setInt16(44 + i * 2, samples[i] ?? 0, true);
	}
	return new Blob([buffer], { type: "audio/wav" });
}
