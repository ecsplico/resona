import { describe, it, expect, vi, beforeEach } from "vitest";
import { nextTick } from "vue";

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
});
