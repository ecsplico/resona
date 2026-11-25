import { describe, it, expect } from "vitest";
import { catalogueProfile, flattenedSteps, pushedCatalogueProfiles } from "./catalogueProfiles";

// The profile new recordings are transcribed with is a catalogue profile of the hub repository
// (`profiles/` at the monorepo root), and `just push-profiles` uploads it. No hub code is imported.

// useRuntimeConfig needs the nuxt instance, which exists only inside a running test.
function defaultProfile(): string {
	return useRuntimeConfig().public.defaultProfile;
}

describe("default profile", () => {
	it("is the catalogue profile dictation-medical-de, not the bundled generic default", () => {
		expect(defaultProfile()).toBe("dictation-medical-de");
	});

	it("exists in the catalogue as rules only (no LLM step, inline rules)", () => {
		const name = defaultProfile();
		expect(catalogueProfile(name)?.name).toBe(name);
		const steps = flattenedSteps(name);
		expect(steps.every(step => step.type === "replacements" || step.type === "include")).toBe(true);
		expect(steps.some(step => step.source !== undefined)).toBe(false);
		expect(steps.some(step => (step.rules?.length ?? 0) > 0)).toBe(true);
	});

	it("is pushed by just push-profiles", () => {
		expect(pushedCatalogueProfiles()).toContain(defaultProfile());
	});
});
