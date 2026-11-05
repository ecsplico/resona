<script setup lang="ts">
import { watch } from "vue";
import { EditorContent, useEditor } from "@tiptap/vue-3";
import StarterKit from "@tiptap/starter-kit";
import Placeholder from "@tiptap/extension-placeholder";
import type { JSONContent } from "@tiptap/vue-3";

// The transcript is stored as plain text (Directus `text` field). TipTap is used
// purely as the editing surface: we map plain text ↔ paragraphs so exports, copy
// and the batch/live writers keep seeing plain text. One `\n` = one paragraph, so
// `getText({ blockSeparator })` round-trips losslessly.
const BLOCK = "\n";

const props = withDefaults(defineProps<{ modelValue: string; placeholder?: string }>(), {
	placeholder: "",
});

const emit = defineEmits<{ "update:modelValue": [value: string] }>();

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
	extensions: [
		StarterKit,
		Placeholder.configure({ placeholder: props.placeholder }),
	],
	onUpdate: ({ editor }) => {
		const text = editor.getText({ blockSeparator: BLOCK });
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
	e.chain().focus().insertContent(lead + text).run();
}

function focus(): void {
	editor.value?.chain().focus().run();
}

// Insert a placeholder at the current cursor position. Returns the range
// [from, to) so the caller can replace it later with replaceBetween().
function insertPlaceholder(): { from: number; to: number } | null {
	const e = editor.value;
	if (!e) return null;
	const from = e.state.selection.from;
	e.chain().focus().insertContent("[…]").run();
	return { from, to: from + 3 };
}

// Replace the range [from, to) with text, or delete it when text is empty.
function replaceBetween(from: number, to: number, text: string): void {
	const e = editor.value;
	if (!e) return;
	if (text) {
		e.chain().focus().insertContentAt({ from, to }, text).run();
	}
	else {
		e.chain().focus().deleteRange({ from, to }).run();
	}
}

defineExpose({ insertText, insertPlaceholder, replaceBetween, focus });
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
</style>
