import { describe, it, expect } from "vitest";
import { POSTPROCESS_PROFILES } from "~/utils/postprocess";
import { catalogueProfile, flattenedSteps, pushedCatalogueProfiles } from "./catalogueProfiles";

// shared/postprocessProfiles.json names the profiles offered under "Nachbearbeiten"; the
// dictations MCP server offers exactly these (it never reads the hub's live list). They
// must be catalogue profiles of the hub repository that `just push-profiles` uploads,
// and `instructions` must say whether the hub accepts instructions for them.
describe("postprocess profile catalogue", () => {
	it("names exactly the profiles push-profiles uploads besides the transcription default", () => {
		const offered = POSTPROCESS_PROFILES.map(p => p.name);
		const pushed = pushedCatalogueProfiles().filter(name => name !== useRuntimeConfig().public.defaultProfile);
		expect(offered).toEqual(pushed);
	});

	it("marks the profiles with an llm/extract step as taking instructions", () => {
		for (const entry of POSTPROCESS_PROFILES) {
			expect(catalogueProfile(entry.name)?.name).toBe(entry.name);
			const llm = flattenedSteps(entry.name).some(step => step.type === "llm" || step.type === "extract");
			expect(entry.instructions, entry.name).toBe(llm);
			expect(entry.description.length).toBeGreaterThan(0);
		}
	});
});
