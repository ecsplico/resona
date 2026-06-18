import { describe, it, expect } from "vitest";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import HField from "~/components/hearth/HField.vue";

describe("HField", () => {
	it("renders the label when set", async () => {
		const w = await mountSuspended(HField, { props: { label: "Name" } });
		expect(w.find("label").exists()).toBe(true);
		expect(w.find("label").text()).toContain("Name");
	});
	it("does not render a label by default", async () => {
		const w = await mountSuspended(HField);
		expect(w.find("label").exists()).toBe(false);
	});
	it("emits update:modelValue with the typed value", async () => {
		const w = await mountSuspended(HField);
		await w.find("input").setValue("hello");
		expect(w.emitted("update:modelValue")).toBeTruthy();
		expect(w.emitted("update:modelValue")![0]).toEqual(["hello"]);
	});
	it("reflects modelValue as the input value", async () => {
		const w = await mountSuspended(HField, { props: { modelValue: "abc" } });
		expect((w.find("input").element as HTMLInputElement).value).toBe("abc");
	});
	it("focused prop adds h-field__shell--focused", async () => {
		const w = await mountSuspended(HField, { props: { focused: true } });
		expect(w.find(".h-field__shell").classes()).toContain("h-field__shell--focused");
	});
	it("does not add the focused class by default", async () => {
		const w = await mountSuspended(HField);
		expect(w.find(".h-field__shell").classes()).not.toContain("h-field__shell--focused");
	});
	it("mono adds h-field__input--mono", async () => {
		const w = await mountSuspended(HField, { props: { mono: true } });
		expect(w.find("input").classes()).toContain("h-field__input--mono");
	});
	it("does not add the mono class by default", async () => {
		const w = await mountSuspended(HField);
		expect(w.find("input").classes()).not.toContain("h-field__input--mono");
	});
	it("renders the leading icon slot", async () => {
		const w = await mountSuspended(HField, {
			slots: { icon: () => "★" },
		});
		expect(w.text()).toContain("★");
	});
});
