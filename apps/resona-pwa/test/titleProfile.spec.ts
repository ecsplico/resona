import { describe, it, expect } from "vitest";
import { TITLE_PROFILE, titleFromResult } from "~/composables/useTitleJob";

// The profile is checked against the hub's published profile format only
// (docs: postprocessing guide, "Profile file format"); no hub code is imported.
const modules = import.meta.glob<Profile>("../profiles/*.json", { eager: true, import: "default" });
const profiles = new Map(Object.entries(modules).map(([path, profile]) => [path.split("/").pop()!, profile]));
const files = [...profiles.keys()].sort();

interface Step {
	type: string;
	name?: string;
	prompt?: string;
	endpoint?: string;
	model?: string;
	source?: string;
	rules?: unknown;
	temperature?: number;
	max_tokens?: number;
	timeout?: number;
}
interface Profile {
	name: string;
	description?: string;
	initial_prompt?: string[];
	steps: Step[];
	instructions?: string;
}

function load(file: string): Profile {
	return profiles.get(file)!;
}

const PROFILE_KEYS = ["name", "description", "initial_prompt", "steps", "instructions"];
const STEP_TYPES = ["replacements", "llm", "extract"];

describe("app hub profiles", () => {
	it("ships the title profile the app asks for", () => {
		expect(files).toEqual([`${TITLE_PROFILE}.json`]);
	});

	it.each(files)("%s follows the hub profile format", (file) => {
		const profile = load(file);
		expect(Object.keys(profile).filter(key => !PROFILE_KEYS.includes(key))).toEqual([]);
		// `resona profiles push <name> <file>` stores it under the file's name.
		expect(profile.name).toBe(file.replace(/\.json$/, ""));
		expect(profile.name).toMatch(/^[a-z0-9][a-z0-9-]*$/);
		expect(Array.isArray(profile.initial_prompt ?? [])).toBe(true);
		expect(profile.steps.length).toBeGreaterThan(0);
		for (const step of profile.steps) {
			expect(STEP_TYPES).toContain(step.type);
			// Hosted profiles carry rules inline and name an endpoint, never a model or a file.
			expect(step.source).toBeUndefined();
			expect(step.model).toBeUndefined();
			if (step.type === "replacements") {
				expect(Array.isArray(step.rules)).toBe(true);
			}
			else {
				expect(typeof step.prompt).toBe("string");
				expect(step.prompt).not.toBe("");
				// A model role the organization maps to an endpoint (settings model_roles).
				expect(step.endpoint).toMatch(/^@[a-z][a-z0-9_-]*$/);
			}
		}
	});

	it("title-de is one extract step whose result useTitleJob can read", () => {
		const profile = load(`${TITLE_PROFILE}.json`);
		expect(profile.steps).toHaveLength(1);
		const step = profile.steps[0]!;
		expect(step).toMatchObject({ type: "extract", name: "title", endpoint: "@title", temperature: 0, max_tokens: 64, timeout: 30 });
		// Extract data is keyed by step name; the prompt asks for {"title": "..."}.
		expect(step.prompt).toContain("{\"title\": \"...\"}");
		const structured = { [step.name!]: { title: "Befund Kontrolluntersuchung Knie" } };
		expect(titleFromResult({ structured })).toBe("Befund Kontrolluntersuchung Knie");
	});
});
