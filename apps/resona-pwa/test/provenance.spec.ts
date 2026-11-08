import { describe, expect, it } from "vitest";
import { describeProvenance } from "~/utils/provenance";

describe("describeProvenance", () => {
	it("names worker, engine, model, accelerator and duration per stage", () => {
		const lines = describeProvenance({
			worker_id: "wrk_1",
			stages: [
				{ stage: "asr", engine: "whisper", model: "large-v3", accelerator: "cuda", seconds: 12.34 },
				{ stage: "llm", engine: "litellm", model: "litellm", accelerator: "external", seconds: 3.04 },
			],
		});
		expect(lines).toEqual([
			"Worker wrk_1 · asr: whisper · large-v3 (cuda) · 12 s",
			"llm: litellm (external) · 3,0 s",
		]);
	});

	it("returns nothing without provenance and only the worker without stages", () => {
		expect(describeProvenance(null)).toEqual([]);
		expect(describeProvenance({ worker_id: "wrk_1", stages: [] })).toEqual(["Worker wrk_1"]);
	});
});
