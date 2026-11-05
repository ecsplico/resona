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

	type EditorApi = { insertText: (t: string) => void; appendParagraph: (t: string) => void };

	function lastEmit(w: { emitted: (e: string) => unknown[][] | undefined }): string {
		return w.emitted("update:modelValue")!.at(-1)![0] as string;
	}

	it("appendParagraph adds a paragraph at the end and keeps the caret where it was", async () => {
		const w = await mountSuspended(TranscriptEditor, {
			props: { modelValue: "erste zeile\nzweite zeile" },
		});
		await nextTick();
		const vm = w.vm as unknown as EditorApi;
		vm.appendParagraph("neuer teil");
		await nextTick();
		expect(lastEmit(w)).toBe("erste zeile\nzweite zeile\nneuer teil");

		// The caret is still at the start of the document, not after the appended text.
		vm.insertText("X");
		await nextTick();
		expect(lastEmit(w)).toBe("Xerste zeile\nzweite zeile\nneuer teil");
	});

	it("appendParagraph fills an empty document or a trailing empty line without a blank paragraph", async () => {
		const empty = await mountSuspended(TranscriptEditor, { props: { modelValue: "" } });
		await nextTick();
		(empty.vm as unknown as EditorApi).appendParagraph("neuer teil");
		await nextTick();
		expect(lastEmit(empty)).toBe("neuer teil");

		const trailing = await mountSuspended(TranscriptEditor, { props: { modelValue: "erster absatz\n" } });
		await nextTick();
		(trailing.vm as unknown as EditorApi).appendParagraph("neuer teil");
		await nextTick();
		expect(lastEmit(trailing)).toBe("erster absatz\nneuer teil");
	});
});
