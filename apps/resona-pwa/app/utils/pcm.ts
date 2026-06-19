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
