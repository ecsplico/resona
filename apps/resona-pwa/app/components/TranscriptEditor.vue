<script setup lang="ts">
import { watch } from "vue";
import { EditorContent, getTextBetween, getTextSerializersFromSchema, useEditor } from "@tiptap/vue-3";
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
const props = withDefaults(defineProps<{ modelValue: string; placeholder?: string; editable?: boolean }>(), {
	placeholder: "",
	editable: true,
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

const editor = useEditor({
	content: toContent(props.modelValue),
	editable: props.editable,
	extensions: [
		StarterKit,
		Placeholder.configure({ placeholder: props.placeholder }),
		DictationPart,
	],
	onUpdate: ({ editor }) => {
		const text = editor.getText({ blockSeparator: BLOCK });
		if (!programmatic) emit("typed");
		if (text !== props.modelValue) {
			emit("update:modelValue", text);
		}
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

/** Shows a chip for `partId` at the caret, or at the end of the text. */
function insertDictationPart(partId: string, at: "caret" | "end"): void {
	const e = editor.value;
	if (!e || findDictationPart(e.state.doc, partId) !== null) return;
	const node = e.schema.nodes[DICTATION_PART]!.create({ partId, status: "recording" });
	const tr = insertDictationNode(e.state.tr, node, at === "caret" ? e.state.selection.from : null);
	viaApi(() => e.view.dispatch(tr.setMeta(DICTATION_PART_ADD, partId).setMeta("addToHistory", false)));
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
	const at = getTextBetween(doc, { from: 0, to: pos }, {
		blockSeparator: BLOCK,
		textSerializers: getTextSerializersFromSchema(e.schema),
	}).length;
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

defineExpose({
	insertText,
	appendParagraph,
	focus,
	hasFocus,
	insertDictationPart,
	setDictationPartStatus,
	resolveDictationPart,
	removeDictationPart,
});
</script>

<template>
	<div class="tt-editor">
		<EditorContent :editor="editor" />
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
</style>
