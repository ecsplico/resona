import { describe, it, expect, afterEach } from "vitest";
import { nextTick } from "vue";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { TextSelection } from "@tiptap/pm/state";
import type { EditorView } from "@tiptap/pm/view";
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
	type DictationApi = {
		insertDictationPart: (partId: string, at: "caret" | "end") => void;
		setDictationPartStatus: (partId: string, status: "recording" | "transcribing" | "error") => void;
		resolveDictationPart: (partId: string, text: string, options?: { paragraph?: boolean }) => { base: string; at: number; inserted: string } | null;
		removeDictationPart: (partId: string) => void;
	};

	// Chips are observed in the rendered document, as the user sees them.
	function hasChips(w: { findAll: (s: string) => unknown[] }): boolean {
		return w.findAll("[data-dictation-part]").length > 0;
	}

	function viewOf(w: { find: (s: string) => { element: Element } }): EditorView {
		return (w.find(".ProseMirror").element as unknown as { editor: { view: EditorView } }).editor.view;
	}

	function lastEmit(w: { emitted: (e: string) => unknown[][] | undefined }): string {
		return w.emitted("update:modelValue")!.at(-1)![0] as string;
	}

	it("emits typed for user edits, never for the exposed methods (dictation, appended parts, chips)", async () => {
		const w = await mountEditor("erste zeile");
		await nextTick();
		const vm = w.vm as unknown as EditorApi & DictationApi;
		vm.insertText("diktiert");
		vm.appendParagraph("neuer teil");
		vm.insertDictationPart("p1", "end");
		vm.setDictationPartStatus("p1", "transcribing");
		vm.resolveDictationPart("p1", "satz");
		vm.insertDictationPart("p2", "end");
		vm.removeDictationPart("p2");
		await nextTick();
		expect(w.emitted("typed")).toBeUndefined();

		// A transaction dispatched on the view, as keyboard input is.
		const view = viewOf(w);
		view.dispatch(view.state.tr.insertText("!"));
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

	describe("dictation part (chip)", () => {
		const chip = (w: { find: (s: string) => { exists: () => boolean; attributes: (a: string) => string | undefined } }, id: string) =>
			w.find(`[data-dictation-part="${id}"]`);

		it("shows a chip that adds nothing to the emitted text", async () => {
			const w = await mountEditor("Hallo Welt");
			await nextTick();
			const vm = w.vm as unknown as DictationApi;
			vm.insertDictationPart("p1", "end");
			await nextTick();
			expect(chip(w, "p1").exists()).toBe(true);
			expect(chip(w, "p1").attributes("data-label")).toBe("Diktat wird aufgenommen");
			expect(hasChips(w)).toBe(true);
			// Not even a space or a line break: the v-model (and so `dirty`) is untouched.
			expect(w.emitted("update:modelValue")).toBeUndefined();

			vm.setDictationPartStatus("p1", "transcribing");
			await nextTick();
			expect(chip(w, "p1").attributes("data-label")).toBe("Diktat wird transkribiert");
			expect(w.emitted("update:modelValue")).toBeUndefined();
		});

		it("resolve replaces the chip with the text and reports where it went in the plain text", async () => {
			const w = await mountEditor("Hallo Welt");
			await nextTick();
			const vm = w.vm as unknown as DictationApi;
			vm.insertDictationPart("p1", "end");
			const placed = vm.resolveDictationPart("p1", "diktiert");
			await nextTick();
			expect(lastEmit(w)).toBe("Hallo Welt diktiert ");
			expect(placed).toEqual({ base: "Hallo Welt", at: 10, inserted: " diktiert " });
			expect(chip(w, "p1").exists()).toBe(false);
			expect(hasChips(w)).toBe(false);
			expect(w.emitted("typed")).toBeUndefined();
		});

		it("keeps its place while the user types before and after it", async () => {
			const w = await mountEditor("erste zeile\nHallo Welt");
			await nextTick();
			const vm = w.vm as unknown as DictationApi;
			const view = viewOf(w);
			// Caret after "Hallo" in the second paragraph (doc positions: p1 = 0..13, text of p2 from 14).
			view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, 19)));
			vm.insertDictationPart("p1", "caret");
			// The user types at the start of the text and at its end.
			view.dispatch(view.state.tr.insertText("Oh, ", 1));
			view.dispatch(view.state.tr.insertText("!", view.state.doc.content.size - 1));
			await nextTick();
			expect(lastEmit(w)).toBe("Oh, erste zeile\nHallo Welt!");

			const placed = vm.resolveDictationPart("p1", "diktiert");
			await nextTick();
			expect(lastEmit(w)).toBe("Oh, erste zeile\nHallo diktiert Welt!");
			expect(placed).toEqual({ base: "Oh, erste zeile\nHallo Welt!", at: 21, inserted: " diktiert" });
		});

		it("a caret right before the chip stays before the resolved paragraph, so typing continues there", async () => {
			const w = await mountEditor("Hallo Welt");
			await nextTick();
			const vm = w.vm as unknown as DictationApi;
			const view = viewOf(w);
			// "Hallo " is doc positions 1..7: the chip goes in after the space.
			view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, 7)));
			vm.insertDictationPart("p1", "caret");
			// The user puts the caret back right before the chip.
			view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, 7)));
			vm.resolveDictationPart("p1", "diktiert", { paragraph: true });
			await nextTick();
			expect(lastEmit(w)).toBe("Hallo \ndiktiert\nWelt");

			view.dispatch(view.state.tr.insertText("X"));
			await nextTick();
			expect(lastEmit(w)).toBe("Hallo X\ndiktiert\nWelt");
		});

		it("a caret right after the chip moves along behind the resolved text", async () => {
			const w = await mountEditor("Hallo Welt");
			await nextTick();
			const vm = w.vm as unknown as DictationApi;
			const view = viewOf(w);
			view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, 7)));
			// Inserting the chip leaves the caret after it.
			vm.insertDictationPart("p1", "caret");
			vm.resolveDictationPart("p1", "diktiert");
			await nextTick();
			view.dispatch(view.state.tr.insertText("X"));
			await nextTick();
			expect(lastEmit(w)).toBe("Hallo diktiert XWelt");
		});

		it("comes back when the user deletes it, and undo after resolve takes the text out without bringing the chip back", async () => {
			const w = await mountEditor("Hallo Welt");
			await nextTick();
			const vm = w.vm as unknown as DictationApi;
			const view = viewOf(w);
			vm.insertDictationPart("p1", "end");
			const pos = view.state.doc.content.size - 2;
			expect(view.state.doc.nodeAt(pos)?.type.name).toBe("dictationPart");
			view.dispatch(view.state.tr.delete(pos, pos + 1));
			await nextTick();
			expect(hasChips(w)).toBe(true);

			vm.resolveDictationPart("p1", "diktiert");
			await nextTick();
			expect(lastEmit(w)).toBe("Hallo Welt diktiert ");
			// The spec passes no v-model back, so the emit of the original text is
			// skipped (it equals the prop); read the editor's own text instead.
			const editor = (w.find(".ProseMirror").element as unknown as { editor: { commands: { undo: () => boolean }; getText: (o: { blockSeparator: string }) => string } }).editor;
			editor.commands.undo();
			await nextTick();
			expect(editor.getText({ blockSeparator: "\n" })).toBe("Hallo Welt");
			expect(hasChips(w)).toBe(false);
			// Undoing further (the user's delete of the chip) does not resurrect a
			// chip whose dictation already landed.
			editor.commands.undo();
			await nextTick();
			expect(editor.getText({ blockSeparator: "\n" })).toBe("Hallo Welt");
			expect(hasChips(w)).toBe(false);
		});

		it("undoing the user's delete of a chip in flight leaves exactly one chip", async () => {
			const w = await mountEditor("Hallo Welt");
			await nextTick();
			const vm = w.vm as unknown as DictationApi;
			const view = viewOf(w);
			vm.insertDictationPart("p1", "end");
			const pos = view.state.doc.content.size - 2;
			view.dispatch(view.state.tr.delete(pos, pos + 1));
			await nextTick();
			const editor = (w.find(".ProseMirror").element as unknown as { editor: { commands: { undo: () => boolean } } }).editor;
			editor.commands.undo();
			await nextTick();
			let chips = 0;
			view.state.doc.descendants((node) => {
				if (node.type.name === "dictationPart") chips += 1;
			});
			expect(chips).toBe(1);
			expect(vm.resolveDictationPart("p1", "diktiert")).not.toBeNull();
			expect(hasChips(w)).toBe(false);
		});

		it("remove drops the chip without touching the text", async () => {
			const w = await mountEditor("Hallo Welt");
			await nextTick();
			const vm = w.vm as unknown as DictationApi;
			vm.insertDictationPart("p1", "end");
			vm.removeDictationPart("p1");
			await nextTick();
			expect(hasChips(w)).toBe(false);
			expect(vm.resolveDictationPart("p1", "zu spät")).toBeNull();
			expect(w.emitted("update:modelValue")).toBeUndefined();
		});
	});
});
