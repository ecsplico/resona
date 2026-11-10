import { Node, mergeAttributes } from "@tiptap/vue-3";
import type { Node as PMNode, NodeType } from "@tiptap/pm/model";
import { Plugin, PluginKey, type Transaction } from "@tiptap/pm/state";

// A dictation in flight, shown in the editor as an atomic, non-editable chip at
// the place its text will land. It is part of the document only so that it moves
// with the user's edits; it adds nothing to the plain text the editor emits (not
// even a space or a line break), so `v-model`, `dirty` and saves never see it.

export type DictationPartStatus = "recording" | "transcribing" | "error";

export const DICTATION_PART = "dictationPart";

/** Transaction meta: the transaction removes the part with this id on purpose (resolve, discard). */
export const DICTATION_PART_DROP = "dictationPartDrop";

/** Transaction meta: the transaction inserts the part with this id on purpose. */
export const DICTATION_PART_ADD = "dictationPartAdd";

const LABELS: Record<DictationPartStatus, string> = {
	recording: "Diktat wird aufgenommen",
	transcribing: "Diktat wird transkribiert",
	error: "Transkription fehlgeschlagen",
};

export function dictationPartLabel(status: DictationPartStatus): string {
	return LABELS[status] ?? LABELS.transcribing;
}

/** Position of the part with `partId`, or null when the document has none. */
export function findDictationPart(doc: PMNode, partId: string): number | null {
	let found: number | null = null;
	doc.descendants((node, pos) => {
		if (found !== null) return false;
		if (node.type.name === DICTATION_PART && node.attrs.partId === partId) {
			found = pos;
			return false;
		}
		return true;
	});
	return found;
}

function partsOf(doc: PMNode): { partId: string; pos: number; node: PMNode }[] {
	const parts: { partId: string; pos: number; node: PMNode }[] = [];
	doc.descendants((node, pos) => {
		if (node.type.name === DICTATION_PART && typeof node.attrs.partId === "string") {
			parts.push({ partId: node.attrs.partId, pos, node });
		}
		return true;
	});
	return parts;
}

function canHold(doc: PMNode, pos: number, type: NodeType): boolean {
	if (pos < 0 || pos > doc.content.size) return false;
	const $pos = doc.resolve(pos);
	return $pos.parent.inlineContent && $pos.parent.canReplaceWith($pos.index(), $pos.index(), type);
}

/**
 * Inserts `node` at `pos` when a chip may stand there, else at the end of the
 * last text block; a document ending in a block that holds no inline content
 * (code block, list) gets a new paragraph for it.
 */
export function insertDictationNode(tr: Transaction, node: PMNode, pos: number | null): Transaction {
	const { doc } = tr;
	if (pos !== null && canHold(doc, pos, node.type)) return tr.insert(pos, node);
	const end = doc.content.size - 1;
	if (end >= 0 && canHold(doc, end, node.type)) return tr.insert(end, node);
	const paragraph = doc.type.schema.nodes.paragraph;
	return paragraph ? tr.insert(doc.content.size, paragraph.create(null, node)) : tr;
}

const keeper = new PluginKey<ReadonlySet<string>>("dictationPartKeeper");

export const DictationPart = Node.create({
	name: DICTATION_PART,
	group: "inline",
	inline: true,
	atom: true,
	selectable: false,
	draggable: false,

	addAttributes() {
		return {
			partId: { default: null, rendered: false },
			status: { default: "transcribing", rendered: false },
		};
	},

	// No parse rule: a copied chip is not pasted back as a second chip, and its
	// label is CSS-generated, so it is not pasted as text either.
	parseHTML() {
		return [];
	},

	renderHTML({ node, HTMLAttributes }) {
		const status = node.attrs.status as DictationPartStatus;
		return ["span", mergeAttributes(HTMLAttributes, {
			"class": `dictation-part dictation-part--${status}`,
			"data-dictation-part": node.attrs.partId,
			"data-status": status,
			"data-label": dictationPartLabel(status),
			"contenteditable": "false",
			"role": "status",
		})];
	},

	renderText() {
		return "";
	},

	// Exactly one chip per part in flight, whatever the user does: a part removed
	// by anything but its own resolve/discard (Backspace next to it, a selection
	// across it, a cut, an outside reset of the text) comes back at the mapped
	// place, and a chip of a part no longer in flight (brought back by undo/redo
	// after its resolve) or a second chip of one part is removed. The plugin state
	// is the set of parts in flight; its fixes stay out of the undo history.
	addProseMirrorPlugins() {
		return [new Plugin<ReadonlySet<string>>({
			key: keeper,
			state: {
				init: () => new Set<string>(),
				apply(tr, live) {
					const added = tr.getMeta(DICTATION_PART_ADD) as string | undefined;
					const dropped = tr.getMeta(DICTATION_PART_DROP) as string | undefined;
					if (!added && !dropped) return live;
					const next = new Set(live);
					if (added) next.add(added);
					if (dropped) next.delete(dropped);
					return next;
				},
			},
			appendTransaction(transactions, oldState, newState) {
				if (!transactions.some(tr => tr.docChanged)) return null;
				const live = keeper.getState(newState)!;
				const now = partsOf(newState.doc);
				const seen = new Set<string>();
				const stray = now.filter(({ partId }) => {
					if (!live.has(partId) || seen.has(partId)) return true;
					seen.add(partId);
					return false;
				});
				const missing = partsOf(oldState.doc).filter(({ partId }) => live.has(partId) && !seen.has(partId));
				if (stray.length === 0 && missing.length === 0) return null;
				const fix = newState.tr;
				for (const { pos } of stray.reverse()) fix.delete(pos, pos + 1);
				for (const { partId, pos, node } of missing) {
					if (seen.has(partId)) continue;
					seen.add(partId);
					let mapped = pos;
					for (const tr of transactions) mapped = tr.mapping.map(mapped, -1);
					insertDictationNode(fix, node.type.create(node.attrs), fix.mapping.map(mapped, -1));
				}
				return fix.setMeta("addToHistory", false);
			},
		})];
	},
});
