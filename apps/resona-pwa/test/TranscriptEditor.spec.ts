import { describe, it, expect } from "vitest";
import { nextTick } from "vue";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import TranscriptEditor from "~/components/TranscriptEditor.vue";

describe("TranscriptEditor (TipTap)", () => {
	it("renders the initial plain text into the editor", async () => {
		const w = await mountSuspended(TranscriptEditor, {
			props: { modelValue: "Hallo Welt" },
		});
		await nextTick();
		expect(w.text()).toContain("Hallo Welt");
	});

	it("emits plain text (one paragraph per line) when the document changes", async () => {
		const w = await mountSuspended(TranscriptEditor, {
			props: { modelValue: "erste zeile\nzweite zeile" },
		});
		await nextTick();
		const vm = w.vm as unknown as { insertText: (t: string) => void };
		vm.insertText("dazu");
		await nextTick();

		const emits = w.emitted("update:modelValue");
		expect(emits).toBeTruthy();
		const last = emits!.at(-1)![0] as string;
		// Dictated text spliced in with a leading space, line breaks preserved.
		expect(last).toContain("dazu");
		expect(last).toContain("erste zeile");
		expect(last).toContain("zweite zeile");
	});
});
