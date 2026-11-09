import { describe, it, expect, afterEach } from "vitest";
import { nextTick } from "vue";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import TranscriptEditor from "~/components/TranscriptEditor.vue";

// Every editor must be destroyed before the file's DOM environment is torn down:
// ProseMirror's DOMObserver flushes on a timer, and a live editor whose timer fires
// after teardown throws `document is not defined` and fails the run.
const mounted: { unmount: () => void }[] = [];

async function mountEditor(modelValue: string) {
	const w = await mountSuspended(TranscriptEditor, { props: { modelValue } });
	mounted.push(w);
	return w;
}

afterEach(() => {
	for (const w of mounted.splice(0)) w.unmount();
});

describe("TranscriptEditor (TipTap)", () => {
	it("renders the initial plain text into the editor", async () => {
		const w = await mountEditor("Hallo Welt");
		await nextTick();
		expect(w.text()).toContain("Hallo Welt");
	});

	it("emits plain text (one paragraph per line) when the document changes", async () => {
		const w = await mountEditor("erste zeile\nzweite zeile");
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

	it("emits typed for user edits, never for the exposed methods (dictation, appended parts)", async () => {
		const w = await mountEditor("erste zeile");
		await nextTick();
		const vm = w.vm as unknown as EditorApi & {
			insertPlaceholder: () => { from: number; to: number } | null;
			replaceBetween: (from: number, to: number, text: string) => void;
		};
		vm.insertText("diktiert");
		vm.appendParagraph("neuer teil");
		const range = vm.insertPlaceholder()!;
		vm.replaceBetween(range.from, range.to, "satz");
		await nextTick();
		expect(w.emitted("typed")).toBeUndefined();

		// A transaction dispatched on the view, as keyboard input is.
		const editor = (w.find(".ProseMirror").element as unknown as { editor: { view: { state: { tr: { insertText: (t: string) => unknown } }; dispatch: (tr: unknown) => void } } }).editor;
		editor.view.dispatch(editor.view.state.tr.insertText("!"));
		await nextTick();
		expect(w.emitted("typed")).toHaveLength(1);
	});

	it("appendParagraph adds a paragraph at the end and keeps the caret where it was", async () => {
		const w = await mountEditor("erste zeile\nzweite zeile");
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
		const empty = await mountEditor("");
		await nextTick();
		(empty.vm as unknown as EditorApi).appendParagraph("neuer teil");
		await nextTick();
		expect(lastEmit(empty)).toBe("neuer teil");

		const trailing = await mountEditor("erster absatz\n");
		await nextTick();
		(trailing.vm as unknown as EditorApi).appendParagraph("neuer teil");
		await nextTick();
		expect(lastEmit(trailing)).toBe("erster absatz\nneuer teil");
	});
});
