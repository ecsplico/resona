<script setup lang="ts">
import { ref, watch } from "vue";
import { EditorContent, Extension, getTextBetween, getTextSerializersFromSchema, useEditor } from "@tiptap/vue-3";
import StarterKit from "@tiptap/starter-kit";
import Placeholder from "@tiptap/extension-placeholder";
import type { JSONContent } from "@tiptap/vue-3";
import { closeHistory } from "@tiptap/pm/history";
import { TextSelection } from "@tiptap/pm/state";
import {
	DICTATION_PART,
	DICTATION_PART_ADD,
	DICTATION_PART_DROP,
	DictationPart,
	findDictationPart,
	insertDictationNode,
	type DictationPartStatus,
} from "~/utils/dictationPart";

// The transcript is stored as plain text (Directus `text` field). TipTap is used
// purely as the editing surface: we map plain text ↔ paragraphs so exports, copy
// and the batch/live writers keep seeing plain text. One `\n` = one paragraph, so
// `getText({ blockSeparator })` round-trips losslessly.
const BLOCK = "\n";

// `editable: false` locks the text (e.g. while a postprocess profile rewrites it).
// `movable`: paragraphs can be moved up/down (Alt+↑/↓ and a control at the caret's
// paragraph); meant for desktop, phones leave it off.
const props = withDefaults(defineProps<{ modelValue: string; placeholder?: string; editable?: boolean; movable?: boolean }>(), {
	placeholder: "",
	editable: true,
	movable: false,
});

// `typed`: the user changed the text (keyboard, paste, cut, undo); not emitted for
// changes made through the exposed methods (dictation, appended parts, chips).
const emit = defineEmits<{ "update:modelValue": [value: string]; "typed": [] }>();

// True while an exposed method runs its commands; onUpdate fires synchronously
// inside them, so their updates are told apart from user input.
let programmatic = false;

function viaApi(run: () => void): void {
	programmatic = true;
	try {
		run();
	}
	finally {
		programmatic = false;
	}
}

function toContent(text: string): JSONContent {
	return {
		type: "doc",
		content: text.split("\n").map(line => ({
			type: "paragraph",
			...(line ? { content: [{ type: "text", text: line }] } : {}),
		})),
	};
}

// Checked on every key press, so toggling `movable` needs no editor rebuild. With
// it on, the keys are taken even at the first/last block, where they do nothing.
const MoveBlocks = Extension.create({
	name: "moveBlocks",
	priority: 1000,
	addKeyboardShortcuts() {
		const move = (dir: -1 | 1): boolean => {
			if (!props.movable) return false;
			moveBlock(dir);
			return true;
		};
		return {
			"Alt-ArrowUp": () => move(-1),
			"Alt-ArrowDown": () => move(1),
		};
	},
});

// Whether the editor has focus, and where the move control stands: the top of the
// caret's top-level block inside `.tt-editor`, and whether that block is the first/last.
const focused = ref(false);
const moveAt = ref<{ top: number; first: boolean; last: boolean } | null>(null);

const editor = useEditor({
	content: toContent(props.modelValue),
	editable: props.editable,
	extensions: [
		StarterKit,
		Placeholder.configure({ placeholder: props.placeholder }),
		DictationPart,
		MoveBlocks,
	],
	onUpdate: ({ editor }) => {
		const text = editor.getText({ blockSeparator: BLOCK });
		if (!programmatic) emit("typed");
		if (text !== props.modelValue) {
			emit("update:modelValue", text);
		}
	},
	onCreate: ({ editor }) => placeMoveControl(editor),
	onTransaction: ({ editor }) => placeMoveControl(editor),
	onFocus: ({ editor }) => {
		focused.value = true;
		placeMoveControl(editor);
	},
	onBlur: () => {
		focused.value = false;
	},
});

// Re-seed the document when the model changes from the outside (initial seed,
// dictation splice done via commands still funnels through here). Skip when the
// editor already reflects the value to avoid a feedback loop / cursor reset.
watch(() => props.modelValue, (value) => {
	const e = editor.value;
	if (!e || e.getText({ blockSeparator: BLOCK }) === value) {
		return;
	}
	e.commands.setContent(toContent(value), { emitUpdate: false });
});

watch(() => props.editable, (value) => {
	editor.value?.setEditable(value, false);
});

// Insert dictated text at the caret and leave the cursor at the end of the
// insert. `focus()` restores the last selection first, so push-to-talk lands
// where the user left off even though the button stole focus.
function insertText(text: string): void {
	const e = editor.value;
	if (!e) {
		return;
	}
	const { from } = e.state.selection;
	const before = from > 0 ? e.state.doc.textBetween(Math.max(0, from - 1), from, BLOCK, " ") : "";
	const lead = before.length > 0 && !/\s$/.test(before) ? " " : "";
	viaApi(() => e.chain().focus().insertContent(lead + text).run());
}

function focus(): void {
	editor.value?.chain().focus().run();
}

// ── Dictation parts ────────────────────────────────────────────────────────
// A dictation in flight is a chip (`dictationPart` node) in the document: it
// moves with the user's edits and adds nothing to the emitted text. All chip
// changes are programmatic and kept out of the undo history; only the text that
// replaces a chip is one undoable step.

function hasFocus(): boolean {
	return editor.value?.isFocused ?? false;
}

type Editor = NonNullable<typeof editor.value>;

/** Plain-text offset of document position `pos` (chips count for nothing). */
function textOffset(e: Editor, pos: number): number {
	return getTextBetween(e.state.doc, { from: 0, to: pos }, {
		blockSeparator: BLOCK,
		textSerializers: getTextSerializersFromSchema(e.schema),
	}).length;
}

/**
 * Document position at plain-text offset `offset`: inside the text block that
 * holds it, after any chip already standing there. Null past the end.
 */
function posAtOffset(e: Editor, offset: number): number | null {
	let found: number | null = null;
	e.state.doc.descendants((node, pos) => {
		if (found !== null) return false;
		if (!node.isTextblock) return true;
		const start = textOffset(e, pos + 1);
		if (offset < start || offset > start + node.textContent.length) return false;
		let cur = pos + 1;
		let left = offset - start;
		node.forEach((child) => {
			if (found !== null) return;
			if (child.isText && left < child.nodeSize) {
				found = cur + left;
				return;
			}
			if (child.isText) left -= child.nodeSize;
			cur += child.nodeSize;
		});
		if (found === null) found = cur;
		return false;
	});
	return found;
}

/**
 * Shows a chip for `partId` at the caret, at the end of the text, or at a
 * plain-text offset (a stored anchor; past the end means the end).
 */
function insertDictationPart(partId: string, at: "caret" | "end" | number, status: DictationPartStatus = "recording"): void {
	const e = editor.value;
	if (!e || findDictationPart(e.state.doc, partId) !== null) return;
	const node = e.schema.nodes[DICTATION_PART]!.create({ partId, status });
	const pos = at === "caret" ? e.state.selection.from : at === "end" ? null : posAtOffset(e, at);
	const tr = insertDictationNode(e.state.tr, node, pos);
	viaApi(() => e.view.dispatch(tr.setMeta(DICTATION_PART_ADD, partId).setMeta("addToHistory", false)));
}

/** Where each chip stands in the emitted plain text `base`: at offset `at`. */
function dictationPartOffsets(): { partId: string; base: string; at: number }[] {
	const e = editor.value;
	if (!e) return [];
	const base = e.getText({ blockSeparator: BLOCK });
	const out: { partId: string; base: string; at: number }[] = [];
	e.state.doc.descendants((node, pos) => {
		if (node.type.name === DICTATION_PART && typeof node.attrs.partId === "string") {
			out.push({ partId: node.attrs.partId, base, at: textOffset(e, pos) });
		}
		return true;
	});
	return out;
}

function setDictationPartStatus(partId: string, status: DictationPartStatus): void {
	const e = editor.value;
	const pos = e ? findDictationPart(e.state.doc, partId) : null;
	if (!e || pos === null) return;
	const node = e.state.doc.nodeAt(pos)!;
	if (node.attrs.status === status) return;
	const tr = e.state.tr.setNodeMarkup(pos, undefined, { ...node.attrs, status });
	viaApi(() => e.view.dispatch(tr.setMeta("addToHistory", false)));
}

/** Removes the chip of `partId` (outside the undo history); returns where it was. */
function removeDictationPart(partId: string): number | null {
	const e = editor.value;
	const pos = e ? findDictationPart(e.state.doc, partId) : null;
	if (!e || pos === null) return null;
	const tr = e.state.tr.delete(pos, pos + 1).setMeta(DICTATION_PART_DROP, partId).setMeta("addToHistory", false);
	viaApi(() => e.view.dispatch(tr));
	return pos;
}

/**
 * Replaces the chip with `text` as one undoable step. Inline (push-to-talk): the
 * text is separated by a space from a word right before it and followed by one
 * unless whitespace follows. `paragraph` (re-dictation): the text becomes its own
 * paragraph(s), splitting the paragraph the chip stands in. Returns where it went
 * in the emitted plain text: `base` (the text without it), the offset `at` and the
 * exact `inserted` string, so `base` with `inserted` at `at` is the new text. Null
 * when there is no such chip; an empty `text` only removes the chip.
 */
function resolveDictationPart(
	partId: string,
	text: string,
	options: { paragraph?: boolean } = {},
): { base: string; at: number; inserted: string } | null {
	const e = editor.value;
	const chipAt = e ? findDictationPart(e.state.doc, partId) : null;
	if (!e || chipAt === null) return null;
	const base = e.getText({ blockSeparator: BLOCK });
	// Where the user's selection was relative to the chip: an end right before it
	// (or earlier) stays before the inserted text, one after it moves along.
	const selection = e.state.selection instanceof TextSelection ? e.state.selection : null;
	const pos = removeDictationPart(partId)!;
	if (!text) return null;
	const { doc } = e.state;
	const at = textOffset(e, pos);
	const $pos = doc.resolve(pos);
	const before = doc.textBetween($pos.start(), pos, BLOCK, "");
	const after = doc.textBetween(pos, $pos.end(), BLOCK, "");
	const tr = e.state.tr;
	let inserted: string;
	if (options.paragraph) {
		// Split off the text after the chip, then open an empty paragraph after the
		// text before it; each line of `text` is one paragraph (`BLOCK`).
		let cur = pos;
		if (after.length > 0) tr.split(cur);
		if (before.length > 0) {
			tr.split(cur);
			cur += 2;
		}
		text.split(BLOCK).forEach((line, i) => {
			if (i > 0) {
				tr.split(cur);
				cur += 2;
			}
			if (line) {
				tr.insertText(line, cur);
				cur += line.length;
			}
		});
		inserted = (before.length > 0 ? BLOCK : "") + text + (after.length > 0 ? BLOCK : "");
	}
	else {
		const lead = before.length > 0 && !/\s$/.test(before) ? " " : "";
		const trail = after.length > 0 && /^\s/.test(after) ? "" : " ";
		inserted = lead + text + trail;
		tr.insertText(inserted, pos);
	}
	if (selection) {
		const keep = (x: number) => (x <= chipAt ? tr.mapping.map(x, -1) : tr.mapping.map(x - 1, 1));
		tr.setSelection(TextSelection.create(tr.doc, keep(selection.anchor), keep(selection.head)));
	}
	// Its own undo step, not merged into typing right before it.
	viaApi(() => e.view.dispatch(closeHistory(tr)));
	return { base, at, inserted };
}

// Append `text` as a new paragraph at the end of the document without moving
// the caret or the selection; it is one undoable step and reaches the v-model
// through onUpdate. An empty last paragraph (empty document or a trailing line
// break) is filled instead of leaving a blank line before the text.
function appendParagraph(text: string): void {
	const e = editor.value;
	if (!e) return;
	const { doc } = e.state;
	const end = doc.content.size;
	const last = doc.lastChild;
	const from = last && last.type.name === "paragraph" && last.content.size === 0 ? end - last.nodeSize : end;
	viaApi(() => e.commands.insertContentAt({ from, to: end }, toContent(text).content ?? [], { updateSelection: false }));
}

// ── Moving paragraphs ──────────────────────────────────────────────────────

/** What the move helpers read from an editor (the hooks hand over the core type). */
type EditorView = Pick<Editor, "state" | "view">;

/** The top-level block holding the caret: its index and start position. */
function caretBlock(e: EditorView): { index: number; start: number } | null {
	const { $head } = e.state.selection;
	return $head.depth < 1 ? null : { index: $head.index(0), start: $head.before(1) };
}

function placeMoveControl(e: EditorView): void {
	const block = caretBlock(e);
	const dom = block ? e.view.nodeDOM(block.start) : null;
	moveAt.value = block && dom instanceof HTMLElement
		? { top: dom.offsetTop, first: block.index === 0, last: block.index === e.state.doc.childCount - 1 }
		: null;
}

/**
 * Moves the caret's top-level block one block up (-1) or down (1), keeping the
 * caret at its place inside it; nothing at the first/last block. A user edit:
 * it reaches onUpdate as typing does (`typed`) and is one undo step of its own.
 */
function moveBlock(dir: -1 | 1): void {
	const e = editor.value;
	const block = e?.isEditable ? caretBlock(e) : null;
	const other = block ? e!.state.doc.maybeChild(block.index + dir) : null;
	if (!e || !block || !other) return;
	const { doc, selection } = e.state;
	const node = doc.child(block.index);
	// The block and its neighbour swap in one replace step, so a chip inside either
	// is in the document exactly once before and after: the keeper plugin leaves it be.
	const from = dir < 0 ? block.start - other.nodeSize : block.start;
	const tr = e.state.tr.replaceWith(from, from + node.nodeSize + other.nodeSize, dir < 0 ? [node, other] : [other, node]);
	const shift = block.start - (dir < 0 ? from : from + other.nodeSize);
	const end = block.start + node.nodeSize;
	const head = selection.head - shift;
	const anchor = selection.anchor >= block.start && selection.anchor <= end ? selection.anchor - shift : head;
	tr.setSelection(TextSelection.between(tr.doc.resolve(anchor), tr.doc.resolve(head))).scrollIntoView();
	e.view.dispatch(closeHistory(tr));
	// Close the group after the move as well, so typing right after it is not undone with it.
	e.view.dispatch(closeHistory(e.state.tr).setMeta("addToHistory", false));
}

defineExpose({
	insertText,
	appendParagraph,
	focus,
	hasFocus,
	insertDictationPart,
	dictationPartOffsets,
	setDictationPartStatus,
	resolveDictationPart,
	removeDictationPart,
});
</script>

<template>
	<div
		class="tt-editor"
		:class="{ 'tt-editor--movable': movable }"
	>
		<EditorContent :editor="editor" />
		<!-- A span, not a div: `.tt-editor > div` is the editor's own flex column. -->
		<span
			v-if="movable && editable"
			v-show="focused && moveAt"
			class="tt-move"
			:style="{ top: `${moveAt?.top ?? 0}px` }"
		>
			<!-- mousedown.prevent keeps the focus (and the caret) in the editor; the -->
			<!-- keyboard path is Alt+↑/↓, so the buttons stay out of the tab order. -->
			<button
				type="button"
				class="tt-move__btn"
				aria-label="Absatz nach oben"
				title="Absatz nach oben (Alt+↑)"
				tabindex="-1"
				:disabled="!moveAt || moveAt.first"
				@mousedown.prevent
				@click="moveBlock(-1)"
			>
				<svg
					viewBox="0 0 12 12"
					aria-hidden="true"
				><path d="M2.5 7.5 6 4l3.5 3.5" /></svg>
			</button>
			<button
				type="button"
				class="tt-move__btn"
				aria-label="Absatz nach unten"
				title="Absatz nach unten (Alt+↓)"
				tabindex="-1"
				:disabled="!moveAt || moveAt.last"
				@mousedown.prevent
				@click="moveBlock(1)"
			>
				<svg
					viewBox="0 0 12 12"
					aria-hidden="true"
				><path d="M2.5 4.5 6 8l3.5-3.5" /></svg>
			</button>
		</span>
	</div>
</template>

<style scoped>
.tt-editor { width:100%; box-sizing:border-box; display:flex; flex-direction:column; background:var(--white); border:1px solid var(--border); border-radius:12px; transition:border-color var(--motion-fast) var(--motion-ease); }
.tt-editor > :deep(div) { flex:1; display:flex; flex-direction:column; }
.tt-editor:focus-within { border-color:var(--accent); }
.tt-editor :deep(.ProseMirror) { flex:1; min-height:240px; box-sizing:border-box; padding:16px 18px; outline:none; white-space:pre-wrap; font-family:var(--font-body); font-size:16px; line-height:1.6; color:var(--fg1); }
.tt-editor :deep(.ProseMirror p) { margin:0 0 0.6em; }
.tt-editor :deep(.ProseMirror p:last-child) { margin-bottom:0; }
.tt-editor :deep(.ProseMirror p.is-editor-empty:first-child::before) { content:attr(data-placeholder); float:left; height:0; pointer-events:none; color:var(--fg3); }
/* Dictation chip: the label is generated, so the node holds no text to copy or paste. */
.tt-editor :deep(.dictation-part) { display:inline-flex; align-items:center; gap:6px; margin:0 2px; padding:1px 10px; border-radius:999px; background:var(--accent-soft); color:var(--accent-ink); font-size:13px; font-weight:600; line-height:1.6; white-space:nowrap; vertical-align:baseline; user-select:none; cursor:default; }
.tt-editor :deep(.dictation-part::before) { content:""; width:7px; height:7px; border-radius:50%; background:currentColor; animation:dictation-pulse 1.2s var(--motion-ease) infinite alternate; }
.tt-editor :deep(.dictation-part::after) { content:attr(data-label) "…"; }
.tt-editor :deep(.dictation-part--error) { background:var(--tomato-soft); color:var(--tomato-deep); }
.tt-editor :deep(.dictation-part--error::before) { animation:none; }
.tt-editor :deep(.dictation-part--error::after) { content:attr(data-label); }
@keyframes dictation-pulse { from { opacity:1; } to { opacity:0.3; } }
@media (prefers-reduced-motion:reduce) { .tt-editor :deep(.dictation-part::before) { animation:none; } }
/* Move control: in a left gutter, at the top of the caret's paragraph. */
.tt-editor--movable { position:relative; }
.tt-editor--movable :deep(.ProseMirror) { padding-left:40px; }
.tt-move { position:absolute; left:8px; display:flex; flex-direction:column; gap:2px; transition:top var(--motion-fast) var(--motion-ease); }
.tt-move__btn { width:22px; height:20px; padding:0; display:inline-flex; align-items:center; justify-content:center; border:1px solid var(--border-strong); border-radius:6px; background:var(--white); color:var(--fg2); cursor:pointer; transition:color var(--motion-fast) var(--motion-ease), border-color var(--motion-fast) var(--motion-ease); }
.tt-move__btn:hover:not(:disabled) { color:var(--accent); border-color:var(--accent); }
.tt-move__btn:disabled { opacity:0.35; cursor:default; }
.tt-move__btn svg { width:12px; height:12px; fill:none; stroke:currentColor; stroke-width:1.6; stroke-linecap:round; stroke-linejoin:round; }
@media (prefers-reduced-motion:reduce) { .tt-move, .tt-move__btn { transition:none; } }
</style>
