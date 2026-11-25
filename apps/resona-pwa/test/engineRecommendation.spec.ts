import { describe, it, expect } from "vitest";
import { engineBadge, engineRoles, type EngineRecommendation } from "../app/utils/engineRecommendation";

function rec(role: EngineRecommendation["role"]): EngineRecommendation {
	return { role, error_percent: 5, seconds_per_30s: 2, basis: "baseline" };
}

describe("engine recommendation badges", () => {
	it("is empty for an unmeasured engine", () => {
		expect(engineBadge({ candidates: [{}] })).toBe("");
		expect(engineBadge({ candidates: [] })).toBe("");
	});

	it("names each role once in German, in role order", () => {
		const engine = { candidates: [{ recommendation: rec("accurate") }, { recommendation: rec("recommended") }, { recommendation: rec("accurate") }] };
		expect(engineRoles(engine)).toEqual(["recommended", "accurate"]);
		expect(engineBadge(engine)).toBe("empfohlen, genau");
		expect(engineBadge({ candidates: [{ recommendation: rec("fast") }] })).toBe("schnell");
	});
});
