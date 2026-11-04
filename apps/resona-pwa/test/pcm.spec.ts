import { describe, it, expect } from "vitest";
import { floatTo16kPCM, pcmToWav } from "~/utils/pcm";

describe("floatTo16kPCM", () => {
	it("returns an Int16Array", () => {
		const out = floatTo16kPCM(new Float32Array([0, 0.5, -0.5]), 16000);
		expect(out).toBeInstanceOf(Int16Array);
	});

	it("does not resample when input rate equals target", () => {
		const input = new Float32Array([0, 0.25, -0.25, 1]);
		const out = floatTo16kPCM(input, 16000);
		expect(out.length).toBe(input.length);
	});

	it("downsamples 32 kHz to ~16 kHz (half the samples)", () => {
		const input = new Float32Array(32000).fill(0);
		const out = floatTo16kPCM(input, 32000);
		// floor(32000 * 16000 / 32000) = 16000
		expect(out.length).toBe(16000);
	});

	it("downsamples 48 kHz to 16 kHz (one third of the samples)", () => {
		const input = new Float32Array(48000).fill(0);
		const out = floatTo16kPCM(input, 48000);
		expect(out.length).toBe(16000);
	});

	it("clamps values above 1.0 to 32767 and below -1.0 to -32768", () => {
		const out = floatTo16kPCM(new Float32Array([2, -2]), 16000);
		expect(out[0]).toBe(32767);
		expect(out[1]).toBe(-32768);
	});

	it("encodes full-scale values to int16 extremes", () => {
		const out = floatTo16kPCM(new Float32Array([1, -1]), 16000);
		expect(out[0]).toBe(32767);
		expect(out[1]).toBe(-32768);
	});

	it("encodes zero to zero", () => {
		const out = floatTo16kPCM(new Float32Array([0]), 16000);
		expect(out[0]).toBe(0);
	});
});

describe("pcmToWav", () => {
	async function bytes(blob: Blob): Promise<DataView> {
		return new DataView(await blob.arrayBuffer());
	}
	function tag(view: DataView, offset: number): string {
		return String.fromCharCode(...[0, 1, 2, 3].map(i => view.getUint8(offset + i)));
	}

	it("returns an audio/wav blob of 44 header bytes plus 2 bytes per sample", () => {
		const blob = pcmToWav(new Int16Array(10), 16000);
		expect(blob.type).toBe("audio/wav");
		expect(blob.size).toBe(44 + 20);
	});

	it("writes RIFF/WAVE/fmt /data tags", async () => {
		const view = await bytes(pcmToWav(new Int16Array(4), 16000));
		expect(tag(view, 0)).toBe("RIFF");
		expect(tag(view, 8)).toBe("WAVE");
		expect(tag(view, 12)).toBe("fmt ");
		expect(tag(view, 36)).toBe("data");
	});

	it("writes mono 16-bit PCM format fields and the sample rate", async () => {
		const view = await bytes(pcmToWav(new Int16Array(4), 22050));
		expect(view.getUint16(20, true)).toBe(1);
		expect(view.getUint16(22, true)).toBe(1);
		expect(view.getUint32(24, true)).toBe(22050);
		expect(view.getUint32(28, true)).toBe(44100);
		expect(view.getUint16(32, true)).toBe(2);
		expect(view.getUint16(34, true)).toBe(16);
	});

	it("writes data length and RIFF size, then little-endian samples", async () => {
		const view = await bytes(pcmToWav(new Int16Array([1, -2, 32767]), 16000));
		expect(view.getUint32(40, true)).toBe(6);
		expect(view.getUint32(4, true)).toBe(36 + 6);
		expect(view.getInt16(44, true)).toBe(1);
		expect(view.getInt16(46, true)).toBe(-2);
		expect(view.getInt16(48, true)).toBe(32767);
	});
});
