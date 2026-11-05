import { describe, it, expect } from "vitest";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { defineComponent, h } from "vue";

const Probe = defineComponent({ setup: () => () => h("div", { style: "color: var(--accent)" }, "x") });

describe("tokens", () => {
	it("accent token name resolves on :root via getComputedStyle", async () => {
		await mountSuspended(Probe);
		const root = document.documentElement;
		root.style.setProperty("--accent", "#D96846");
		expect(getComputedStyle(root).getPropertyValue("--accent").trim()).toBe("#D96846");
	});
});
