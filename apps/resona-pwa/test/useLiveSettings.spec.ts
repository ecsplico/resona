import { describe, it, expect, vi, beforeEach } from "vitest";
import { nextTick } from "vue";
import { effectiveLiveSettings, type LiveSettings } from "~/composables/useLiveSettings";

const KEY = "resona.live.settings";

// The composable is a module singleton that loads once — re-import per test.
async function fresh() {
	vi.resetModules();
	const mod = await import("~/composables/useLiveSettings");
	return mod.useLiveSettings();
}

describe("useLiveSettings", () => {
	beforeEach(() => {
		window.localStorage.clear();
	});

	it("starts from the specified defaults", async () => {
		expect({ ...(await fresh()) }).toEqual({
			source: "hub",
			hubEngine: "",
			correction: false,
			correctionEngine: "",
			chunkSeconds: 15,
			cleanup: false,
		});
	});

	it("loads saved values and clamps chunkSeconds to 8–30", async () => {
		window.localStorage.setItem(KEY, JSON.stringify({ source: "device", hubEngine: "whisper", correction: true, correctionEngine: "big", chunkSeconds: 3 }));
		const low = await fresh();
		expect(low.source).toBe("device");
		expect(low.hubEngine).toBe("whisper");
		expect(low.correction).toBe(true);
		expect(low.correctionEngine).toBe("big");
		expect(low.chunkSeconds).toBe(8);

		window.localStorage.setItem(KEY, JSON.stringify({ chunkSeconds: 99 }));
		expect((await fresh()).chunkSeconds).toBe(30);

		window.localStorage.setItem(KEY, JSON.stringify({ chunkSeconds: 30 }));
		expect((await fresh()).chunkSeconds).toBe(30);
	});

	it("ignores invalid saved values and corrupt JSON", async () => {
		window.localStorage.setItem(KEY, JSON.stringify({ source: "cloud", hubEngine: 5, correction: "yes" }));
		const s = await fresh();
		expect(s.source).toBe("hub");
		expect(s.hubEngine).toBe("");
		expect(s.correction).toBe(false);

		window.localStorage.setItem(KEY, "{not json");
		expect((await fresh()).source).toBe("hub");
	});

	it("persists changes to localStorage and shares one object", async () => {
		const s = await fresh();
		const mod = await import("~/composables/useLiveSettings");
		expect(mod.useLiveSettings()).toBe(s);
		s.source = "device";
		s.hubEngine = "parakeet";
		await nextTick();
		expect(JSON.parse(window.localStorage.getItem(KEY)!)).toMatchObject({ source: "device", hubEngine: "parakeet" });
	});

	it("loads a saved cleanup toggle and ignores a non-boolean one", async () => {
		window.localStorage.setItem(KEY, JSON.stringify({ cleanup: true }));
		expect((await fresh()).cleanup).toBe(true);
		window.localStorage.setItem(KEY, JSON.stringify({ cleanup: "yes" }));
		expect((await fresh()).cleanup).toBe(false);
	});
});

describe("effectiveLiveSettings", () => {
	const device = (over: Partial<LiveSettings> = {}): LiveSettings => ({
		source: "hub",
		hubEngine: "",
		correction: true,
		correctionEngine: "",
		chunkSeconds: 15,
		cleanup: false,
		...over,
	});
	const org = { engine: "parakeet", correction_execution_profile: "gpu-private", cleanup_profile: "dictation-clean-de" };

	it("empty device values inherit the organization's live defaults", () => {
		expect(effectiveLiveSettings(device(), org)).toEqual({
			hubEngine: "parakeet",
			hubEngineInherited: true,
			correctionEngine: "",
			correctionExecutionProfile: "gpu-private",
			cleanupProfile: "",
		});
	});

	it("device values win over the organization", () => {
		expect(effectiveLiveSettings(device({ hubEngine: "whisper", correctionEngine: "openai" }), org)).toEqual({
			hubEngine: "whisper",
			hubEngineInherited: false,
			correctionEngine: "openai",
			correctionExecutionProfile: "",
			cleanupProfile: "",
		});
	});

	it("cleanup runs the org profile only when the device toggle is on", () => {
		expect(effectiveLiveSettings(device({ cleanup: true }), org).cleanupProfile).toBe("dictation-clean-de");
		expect(effectiveLiveSettings(device({ cleanup: true }), {}).cleanupProfile).toBe("");
	});

	it("without org defaults (not loaded, no token) the device values stand alone", () => {
		expect(effectiveLiveSettings(device({ cleanup: true }), null)).toEqual({
			hubEngine: "",
			hubEngineInherited: false,
			correctionEngine: "",
			correctionExecutionProfile: "",
			cleanupProfile: "",
		});
	});
});
