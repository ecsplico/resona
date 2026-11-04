import { describe, it, expect, afterEach } from "vitest";
import { useDataStore } from "~/composables/useDataStore";

// Uses the real nuxt runtimeConfig (mocking useRuntimeConfig breaks Nuxt init)
// and overrides the public dataMode for the test only. Config access must
// happen inside test/hooks, where the Nuxt app context is available.
describe("useDataStore", () => {
	let original: string | undefined;

	afterEach(() => {
		useRuntimeConfig().public.dataMode = original as string;
	});

	it("throws on an unknown data mode", () => {
		const cfg = useRuntimeConfig().public;
		original = cfg.dataMode;
		cfg.dataMode = "bogus";
		expect(() => useDataStore()).toThrow("NUXT_PUBLIC_DATA_MODE must be \"directus\" or \"standalone\"");
	});
});
