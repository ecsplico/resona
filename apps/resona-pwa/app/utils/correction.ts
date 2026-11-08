import type { CorrectionEdit } from "~/types/directus";

// Above this many token pairs the LCS table gets too big for a phone; the
// changed middle is then reported as one single edit.
const MAX_LCS_CELLS = 4_000_000;

function tokens(text: string): string[] {
	return text.split(/(\s+)/).filter(part => part.length > 0);
}

/**
 * Changed spans between the engine/previous text and the saved text, on word level:
 * shared prefix and suffix are trimmed, the middle is aligned by LCS, adjacent
 * differences are merged. Empty when nothing changed.
 */
export function diffEdits(before: string, after: string): CorrectionEdit[] {
	if (before === after) return [];
	const a = tokens(before);
	const b = tokens(after);
	let start = 0;
	while (start < a.length && start < b.length && a[start] === b[start]) start += 1;
	let endA = a.length;
	let endB = b.length;
	while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
		endA -= 1;
		endB -= 1;
	}
	const midA = a.slice(start, endA);
	const midB = b.slice(start, endB);
	if (midA.length * midB.length > MAX_LCS_CELLS) {
		return [{ before: midA.join(""), after: midB.join("") }];
	}
	const rows = midA.length + 1;
	const cols = midB.length + 1;
	const table = new Uint32Array(rows * cols);
	for (let i = midA.length - 1; i >= 0; i -= 1) {
		for (let j = midB.length - 1; j >= 0; j -= 1) {
			table[i * cols + j] = midA[i] === midB[j]
				? table[(i + 1) * cols + j + 1]! + 1
				: Math.max(table[(i + 1) * cols + j]!, table[i * cols + j + 1]!);
		}
	}
	const edits: CorrectionEdit[] = [];
	let cur: CorrectionEdit | null = null;
	let i = 0;
	let j = 0;
	const flush = (): void => {
		if (cur) edits.push(cur);
		cur = null;
	};
	while (i < midA.length || j < midB.length) {
		if (i < midA.length && j < midB.length && midA[i] === midB[j]) {
			// Whitespace-only equal tokens between two changes stay inside one edit.
			if (cur && midA[i]!.trim() === "") {
				cur.before += midA[i];
				cur.after += midB[j];
			}
			else {
				flush();
			}
			i += 1;
			j += 1;
		}
		else if (j < midB.length && (i === midA.length || table[i * cols + j + 1]! >= table[(i + 1) * cols + j]!)) {
			cur ??= { before: "", after: "" };
			cur.after += midB[j];
			j += 1;
		}
		else {
			cur ??= { before: "", after: "" };
			cur.before += midA[i];
			i += 1;
		}
	}
	flush();
	return edits.map(e => ({ before: e.before.trim(), after: e.after.trim() })).filter(e => e.before !== e.after);
}
