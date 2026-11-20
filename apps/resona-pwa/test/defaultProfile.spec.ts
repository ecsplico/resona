import { describe, it, expect } from "vitest";

// The profile new recordings are transcribed with is a catalogue profile of the hub repository
// (`profiles/` at the monorepo root), and `just push-profiles` uploads it. No hub code is imported.
interface Profile {
	name: string;
	steps: { type: string; source?: string; rules?: unknown[] }[];
}

const catalogue = import.meta.glob<Profile>("../../../profiles/*.json", { eager: true, import: "default" });
const pushScripts = import.meta.glob<string>("../scripts/push-profiles.sh", { eager: true, query: "?raw", import: "default" });

function catalogueProfile(name: string): Profile | undefined {
	return Object.entries(catalogue).find(([path]) => path.endsWith(`/${name}.json`))?.[1];
}

function pushedCatalogueProfiles(): string[] {
	const script = Object.values(pushScripts)[0] ?? "";
	const list = /^catalogue_profiles=\(([^)]*)\)$/m.exec(script)?.[1] ?? "";
	return list.split(/\s+/).filter(Boolean);
}

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
		const profile = catalogueProfile(name);
		expect(profile?.name).toBe(name);
		expect(profile?.steps.map(step => step.type)).toEqual(["replacements"]);
		expect(profile?.steps[0]?.source).toBeUndefined();
		expect(profile?.steps[0]?.rules?.length).toBeGreaterThan(0);
	});

	it("is pushed by just push-profiles", () => {
		expect(pushedCatalogueProfiles()).toContain(defaultProfile());
	});
});
