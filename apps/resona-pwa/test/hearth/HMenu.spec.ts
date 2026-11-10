import { describe, it, expect } from "vitest";
import { flushPromises } from "@vue/test-utils";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import HMenu from "~/components/hearth/HMenu.vue";

const items = [
	{ id: "share", label: "Teilen" },
	{ id: "export", label: "Als Markdown exportieren" },
	{ id: "delete", label: "Löschen", danger: true },
];

async function mountOpen() {
	const w = await mountSuspended(HMenu, { props: { label: "Weitere Aktionen", items }, attachTo: document.body });
	await w.find("[data-testid=\"menu-trigger\"]").trigger("click");
	await flushPromises();
	return w;
}

describe("HMenu", () => {
	it("is a labelled menu button that opens with focus on the first item", async () => {
		const w = await mountOpen();
		const trigger = w.find("[data-testid=\"menu-trigger\"]");
		expect(trigger.attributes("aria-haspopup")).toBe("menu");
		expect(trigger.attributes("aria-label")).toBe("Weitere Aktionen");
		expect(trigger.attributes("aria-expanded")).toBe("true");
		expect(w.find("[role=\"menu\"]").exists()).toBe(true);
		expect(w.findAll("[role=\"menuitem\"]").map(i => i.text())).toEqual(["Teilen", "Als Markdown exportieren", "Löschen"]);
		expect(document.activeElement).toBe(w.find("[data-testid=\"menu-share\"]").element);
		expect(w.emitted("open")).toHaveLength(1);
		w.unmount();
	});

	it("moves focus with the arrow keys, wrapping at both ends", async () => {
		const w = await mountOpen();
		const menu = w.find("[role=\"menu\"]");
		await menu.trigger("keydown", { key: "ArrowUp" });
		expect(document.activeElement).toBe(w.find("[data-testid=\"menu-delete\"]").element);
		await menu.trigger("keydown", { key: "ArrowDown" });
		expect(document.activeElement).toBe(w.find("[data-testid=\"menu-share\"]").element);
		await menu.trigger("keydown", { key: "ArrowDown" });
		expect(document.activeElement).toBe(w.find("[data-testid=\"menu-export\"]").element);
		w.unmount();
	});

	it("closes on Escape and gives focus back to the trigger", async () => {
		const w = await mountOpen();
		await w.find("[role=\"menu\"]").trigger("keydown", { key: "Escape" });
		expect(w.find("[role=\"menu\"]").exists()).toBe(false);
		expect(document.activeElement).toBe(w.find("[data-testid=\"menu-trigger\"]").element);
		expect(w.emitted("select")).toBeFalsy();
		w.unmount();
	});

	it("closes on a tap outside without selecting anything or taking focus", async () => {
		const w = await mountOpen();
		const outside = document.createElement("button");
		document.body.appendChild(outside);
		outside.focus();
		outside.dispatchEvent(new Event("pointerdown", { bubbles: true }));
		await flushPromises();
		expect(w.find("[role=\"menu\"]").exists()).toBe(false);
		expect(w.emitted("select")).toBeFalsy();
		expect(document.activeElement).toBe(outside);
		outside.remove();
		w.unmount();
	});

	it("emits the chosen item, closes and returns focus", async () => {
		const w = await mountOpen();
		await w.find("[data-testid=\"menu-delete\"]").trigger("click");
		expect(w.emitted("select")).toEqual([["delete"]]);
		expect(w.find("[role=\"menu\"]").exists()).toBe(false);
		expect(document.activeElement).toBe(w.find("[data-testid=\"menu-trigger\"]").element);
		w.unmount();
	});
});
