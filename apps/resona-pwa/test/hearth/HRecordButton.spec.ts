import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import HRecordButton from "~/components/hearth/HRecordButton.vue";

describe("HRecordButton", () => {
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	async function mount(props: Record<string, unknown> = {}) {
		return await mountSuspended(HRecordButton, { props: { label: "Aufnehmen", ...props } });
	}

	it("a short press is a tap", async () => {
		const w = await mount();
		await w.trigger("pointerdown", { pointerId: 1, button: 0 });
		vi.advanceTimersByTime(200);
		await w.trigger("pointerup", { pointerId: 1 });
		expect(Object.keys(w.emitted())).toEqual(expect.arrayContaining(["press", "tap"]));
		expect(w.emitted("hold-start")).toBeUndefined();
		expect(w.emitted("hold-end")).toBeUndefined();
	});

	it("a long press starts a hold on time and ends it on release, without a tap", async () => {
		const w = await mount();
		await w.trigger("pointerdown", { pointerId: 1, button: 0 });
		expect(w.emitted("press")).toHaveLength(1);
		vi.advanceTimersByTime(450);
		expect(w.emitted("hold-start")).toHaveLength(1);
		await w.trigger("pointerup", { pointerId: 1 });
		expect(w.emitted("hold-end")).toHaveLength(1);
		expect(w.emitted("tap")).toBeUndefined();
	});

	it("a cancelled press is no tap, and a cancelled hold ends", async () => {
		const w = await mount();
		await w.trigger("pointerdown", { pointerId: 1, button: 0 });
		await w.trigger("pointercancel", { pointerId: 1 });
		expect(w.emitted("tap")).toBeUndefined();
		await w.trigger("pointerdown", { pointerId: 1, button: 0 });
		vi.advanceTimersByTime(450);
		await w.trigger("pointercancel", { pointerId: 1 });
		expect(w.emitted("hold-end")).toHaveLength(1);
	});

	it("a keyboard activation is a tap; a disabled button emits nothing", async () => {
		const w = await mount();
		await w.trigger("click", { detail: 0 });
		expect(w.emitted("tap")).toHaveLength(1);
		const off = await mount({ disabled: true });
		await off.trigger("pointerdown", { pointerId: 1, button: 0 });
		vi.advanceTimersByTime(450);
		await off.trigger("pointerup", { pointerId: 1 });
		expect(off.emitted("press")).toBeUndefined();
		expect(off.emitted("hold-start")).toBeUndefined();
	});
});
