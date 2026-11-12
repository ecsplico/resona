/** Sampling frequencies by ADTS `sampling_frequency_index` (ISO/IEC 14496-3). */
const SAMPLE_RATES = [96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350];

export interface AdtsScan {
	/** Bytes up to the end of the last whole frame. */
	length: number;
	/** Whole frames found. */
	frames: number;
	/** Seconds of audio in those frames; 0 without a frame. */
	seconds: number;
}

/**
 * Walks the ADTS frames from the start of `bytes`: a recording that ended in a
 * crash or kill can stop in the middle of a frame, and only whole frames are audio.
 * Stops at the first byte that is not a frame header or a frame that runs past the end.
 */
export function scanAdts(bytes: Uint8Array): AdtsScan {
	let offset = 0;
	let frames = 0;
	let samples = 0;
	let rate = 0;
	while (offset + 7 <= bytes.length) {
		const b1 = bytes[offset + 1]!;
		// Sync word 0xFFF, layer 00.
		if (bytes[offset] !== 0xFF || (b1 & 0xF6) !== 0xF0) break;
		const b2 = bytes[offset + 2]!;
		const b3 = bytes[offset + 3]!;
		const frameRate = SAMPLE_RATES[(b2 >> 2) & 0x0F];
		const frameLength = ((b3 & 0x03) << 11) | (bytes[offset + 4]! << 3) | (bytes[offset + 5]! >> 5);
		const headerLength = (b1 & 0x01) === 0 ? 9 : 7;
		if (!frameRate || frameLength < headerLength || offset + frameLength > bytes.length) break;
		rate ||= frameRate;
		samples += 1024 * ((bytes[offset + 6]! & 0x03) + 1);
		frames += 1;
		offset += frameLength;
	}
	return { length: offset, frames, seconds: rate ? samples / rate : 0 };
}
