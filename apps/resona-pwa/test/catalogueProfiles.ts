// Reads the hub repository's profile catalogue (`profiles/` at the monorepo root, blocks in
// `profiles/blocks/`), which `just push-profiles` uploads. No hub code is imported.
export interface CatalogueStep {
	type: string;
	profile?: string;
	source?: string;
	rules?: unknown[];
}

export interface CatalogueProfile {
	name: string;
	steps: CatalogueStep[];
}

const files = import.meta.glob<CatalogueProfile>("../../../profiles/**/*.json", { eager: true, import: "default" });
const pushScripts = import.meta.glob<string>("../scripts/push-profiles.sh", { eager: true, query: "?raw", import: "default" });

export function catalogueProfile(name: string): CatalogueProfile | undefined {
	return Object.values(files).find(profile => profile?.name === name);
}

/**
 * The profile's steps with every `include` of a catalogue profile or block replaced by that
 * profile's steps. An include naming no catalogue file is a block bundled with the hub
 * (`dictation-commands-de`, `filler-words-*`: rules only) and stays as it is.
 */
export function flattenedSteps(name: string, seen: string[] = []): CatalogueStep[] {
	if (seen.includes(name)) throw new Error(`include cycle: ${[...seen, name].join(" → ")}`);
	const profile = catalogueProfile(name);
	if (!profile) return [];
	return profile.steps.flatMap(step =>
		step.type === "include" && step.profile && catalogueProfile(step.profile)
			? flattenedSteps(step.profile, [...seen, name])
			: [step],
	);
}

export function pushedCatalogueProfiles(): string[] {
	const script = Object.values(pushScripts)[0] ?? "";
	const list = /^catalogue_profiles=\(([^)]*)\)$/m.exec(script)?.[1] ?? "";
	return list.split(/\s+/).filter(Boolean);
}
