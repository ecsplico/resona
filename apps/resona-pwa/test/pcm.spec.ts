import { describe, it, expect } from "vitest";
import { floatTo16kPCM } from "~/utils/pcm";

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
