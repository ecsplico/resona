import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import HPlayerDock from "~/components/hearth/HPlayerDock.vue";

describe("HPlayerDock", () => {
	beforeEach(() => {
		vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
		vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it("renders a play button and a progress track", async () => {
		const w = await mountSuspended(HPlayerDock, { props: { src: "blob://x" } });
		expect(w.find("[data-testid=\"player-play\"]").exists()).toBe(true);
		expect(w.find("[data-testid=\"player-track\"]").exists()).toBe(true);
	});

	it("renders a hidden audio element bound to src", async () => {
		const w = await mountSuspended(HPlayerDock, { props: { src: "blob://x" } });
		const audio = w.find("audio");
		expect(audio.exists()).toBe(true);
		expect(audio.attributes("src")).toBe("blob://x");
	});

	it("toggles the is-playing class when the play button is clicked", async () => {
		const w = await mountSuspended(HPlayerDock, { props: { src: "blob://x" } });
		const btn = w.find("[data-testid=\"player-play\"]");
		expect(btn.classes()).not.toContain("is-playing");
		await btn.trigger("click");
		expect(btn.classes()).toContain("is-playing");
		await btn.trigger("click");
		expect(btn.classes()).not.toContain("is-playing");
	});

	it("formats time as M:SS", async () => {
		const w = await mountSuspended(HPlayerDock, { props: { src: "blob://x" } });
		const fmt = (w.vm as unknown as { formatTime: (s: number) => string }).formatTime;
		expect(fmt(0)).toBe("0:00");
		expect(fmt(65)).toBe("1:05");
		expect(fmt(5)).toBe("0:05");
		expect(fmt(Number.NaN)).toBe("0:00");
	});
});
