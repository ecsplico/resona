import { describe, it, expect } from "vitest";
import { POSTPROCESS_PROFILES } from "~/utils/postprocess";

// shared/postprocessProfiles.json names the profiles offered under "Nachbearbeiten"; the
// dictations MCP server offers exactly these (it never reads the hub's live list). They
// must be catalogue profiles of the hub repository that `just push-profiles` uploads,
// and `instructions` must say whether the hub accepts instructions for them.
interface Profile {
	name: string;
	steps: { type: string }[];
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

describe("postprocess profile catalogue", () => {
	it("names exactly the profiles push-profiles uploads besides the transcription default", () => {
		const offered = POSTPROCESS_PROFILES.map(p => p.name);
		const pushed = pushedCatalogueProfiles().filter(name => name !== useRuntimeConfig().public.defaultProfile);
		expect(offered).toEqual(pushed);
	});

	it("marks the profiles with an llm/extract step as taking instructions", () => {
		for (const entry of POSTPROCESS_PROFILES) {
			const profile = catalogueProfile(entry.name);
			expect(profile?.name).toBe(entry.name);
			const llm = (profile?.steps ?? []).some(step => step.type === "llm" || step.type === "extract");
			expect(entry.instructions, entry.name).toBe(llm);
			expect(entry.description.length).toBeGreaterThan(0);
		}
	});
});
