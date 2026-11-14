import { describe, it, expect, afterEach } from "vitest";
import { nextTick } from "vue";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { TextSelection } from "@tiptap/pm/state";
import type { EditorView } from "@tiptap/pm/view";
import type { VueWrapper } from "@vue/test-utils";
import TranscriptEditor from "~/components/TranscriptEditor.vue";

// Every editor must be destroyed before the file's DOM environment is torn down
// (ProseMirror's DOMObserver timer would otherwise fire after teardown).
const mounted: { unmount: () => void }[] = [];

async function mountEditor(modelValue: string, movable = true) {
	const w = await mountSuspended(TranscriptEditor, { props: { modelValue, movable } });
	mounted.push(w);
	await nextTick();
	return w;
}

afterEach(() => {
	for (const w of mounted.splice(0)) w.unmount();
});

type Wrapper = VueWrapper;
type EditorHandle = { view: EditorView; commands: { undo: () => boolean }; getText: (o: { blockSeparator: string }) => string };

function editorOf(w: Wrapper): EditorHandle {
	return (w.find(".ProseMirror").element as unknown as { editor: EditorHandle }).editor;
}

function textOf(w: Wrapper): string {
	return editorOf(w).getText({ blockSeparator: "\n" });
}

/** Puts the caret `offset` positions into top-level block `index`. */
function caretIn(w: Wrapper, index: number, offset: number): void {
	const { view } = editorOf(w);
	let start = 0;
	for (let i = 0; i < index; i += 1) start += view.state.doc.child(i).nodeSize;
	view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, start + 1 + offset)));
}

/** Caret as [top-level block index, offset inside it]. */
function caretAt(w: Wrapper): [number, number] {
	const { $head } = editorOf(w).view.state.selection;
	return [$head.index(0), $head.pos - $head.before(1) - 1];
}

async function press(w: Wrapper, key: "ArrowUp" | "ArrowDown"): Promise<void> {
	editorOf(w).view.dom.dispatchEvent(new KeyboardEvent("keydown", { key, altKey: true, bubbles: true, cancelable: true }));
	await nextTick();
}

function chipCount(w: Wrapper): number {
	let chips = 0;
	editorOf(w).view.state.doc.descendants((node) => {
		if (node.type.name === "dictationPart") chips += 1;
	});
	return chips;
}

describe("TranscriptEditor: moving paragraphs", () => {
	it("Alt+ArrowDown and Alt+ArrowUp move the caret's paragraph and keep the caret in it", async () => {
		const w = await mountEditor("eins\nzwei\ndrei");
		caretIn(w, 0, 2);
		await press(w, "ArrowDown");
		expect(w.emitted("update:modelValue")!.at(-1)![0]).toBe("zwei\neins\ndrei");
		expect(caretAt(w)).toEqual([1, 2]);
		expect(w.emitted("typed")).toHaveLength(1);

		await press(w, "ArrowDown");
		expect(textOf(w)).toBe("zwei\ndrei\neins");
		await press(w, "ArrowUp");
		expect(textOf(w)).toBe("zwei\neins\ndrei");
		expect(caretAt(w)).toEqual([1, 2]);
	});

	it("one undo takes one move back", async () => {
		const w = await mountEditor("eins\nzwei\ndrei");
		caretIn(w, 1, 0);
		await press(w, "ArrowUp");
		expect(textOf(w)).toBe("zwei\neins\ndrei");
		// Typing right after the move is its own undo step.
		const { view } = editorOf(w);
		view.dispatch(view.state.tr.insertText("!"));
		editorOf(w).commands.undo();
		expect(textOf(w)).toBe("zwei\neins\ndrei");
		editorOf(w).commands.undo();
		await nextTick();
		expect(textOf(w)).toBe("eins\nzwei\ndrei");
	});

	it("does nothing at the first and the last paragraph", async () => {
		const w = await mountEditor("eins\nzwei");
		caretIn(w, 0, 1);
		await press(w, "ArrowUp");
		caretIn(w, 1, 1);
		await press(w, "ArrowDown");
		expect(textOf(w)).toBe("eins\nzwei");
		expect(w.emitted("update:modelValue")).toBeUndefined();
	});

	it("a chip in the paragraph moves along and stays a single chip", async () => {
		const w = await mountEditor("Hallo Welt\nzweiter Absatz");
		caretIn(w, 0, 5);
		(w.vm as unknown as { insertDictationPart: (id: string, at: "caret") => void }).insertDictationPart("p1", "caret");
		caretIn(w, 0, 2);
		await press(w, "ArrowDown");
		expect(textOf(w)).toBe("zweiter Absatz\nHallo Welt");
		expect(chipCount(w)).toBe(1);
		const { doc } = editorOf(w).view.state;
		let chipBlock = -1;
		doc.forEach((block, _offset, index) => {
			block.descendants((node) => {
				if (node.type.name === "dictationPart") chipBlock = index;
			});
		});
		expect(chipBlock).toBe(1);
		editorOf(w).commands.undo();
		await nextTick();
		expect(textOf(w)).toBe("Hallo Welt\nzweiter Absatz");
		expect(chipCount(w)).toBe(1);
	});

	it("without movable the keys move nothing and there is no control", async () => {
		const w = await mountEditor("eins\nzwei", false);
		caretIn(w, 0, 1);
		await press(w, "ArrowDown");
		expect(textOf(w)).toBe("eins\nzwei");
		expect(w.find("[aria-label='Absatz nach oben']").exists()).toBe(false);
		expect(w.find("[aria-label='Absatz nach unten']").exists()).toBe(false);
	});

	it("renders the buttons with movable; they move the paragraph and are disabled at the edges", async () => {
		const w = await mountEditor("eins\nzwei");
		caretIn(w, 0, 1);
		await nextTick();
		const up = w.find("[aria-label='Absatz nach oben']");
		const down = w.find("[aria-label='Absatz nach unten']");
		expect(up.exists()).toBe(true);
		expect(up.attributes("title")).toBe("Absatz nach oben (Alt+↑)");
		expect(up.attributes("disabled")).toBeDefined();
		expect(down.attributes("disabled")).toBeUndefined();
		await down.trigger("click");
		expect(textOf(w)).toBe("zwei\neins");
		expect(up.attributes("disabled")).toBeUndefined();
		expect(down.attributes("disabled")).toBeDefined();
	});
});
