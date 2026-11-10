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
	// Tokens as integer ids, so the quadratic loop compares numbers, not strings.
	const ids = new Map<string, number>();
	const idOf = (token: string): number => {
		let found = ids.get(token);
		if (found === undefined) {
			found = ids.size;
			ids.set(token, found);
		}
		return found;
	};
	const idA = Int32Array.from(midA, idOf);
	const idB = Int32Array.from(midB, idOf);
	const rows = midA.length + 1;
	const cols = midB.length + 1;
	// The cell cap bounds the shorter side to 2000 tokens, so an LCS length fits 16 bits.
	const table = new Uint16Array(rows * cols);
	for (let i = midA.length - 1; i >= 0; i -= 1) {
		const row = i * cols;
		const next = row + cols;
		const tokenA = idA[i];
		for (let j = midB.length - 1; j >= 0; j -= 1) {
			table[row + j] = tokenA === idB[j]
				? table[next + j + 1]! + 1
				: Math.max(table[next + j]!, table[row + j + 1]!);
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
		if (i < midA.length && j < midB.length && idA[i] === idB[j]) {
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

/** The one changed span between two texts: `before` with `removed` at `at` replaced by `inserted` gives `after`. */
export interface TextSplice {
	at: number;
	removed: string;
	inserted: string;
}

/** Common prefix and suffix trimmed off: the single span where `after` differs from `before`. */
export function textSplice(before: string, after: string): TextSplice {
	const max = Math.min(before.length, after.length);
	let start = 0;
	while (start < max && before[start] === after[start]) start += 1;
	let end = 0;
	while (end < max - start && before[before.length - 1 - end] === after[after.length - 1 - end]) end += 1;
	return {
		at: start,
		removed: before.slice(start, before.length - end),
		inserted: after.slice(start, after.length - end),
	};
}

/**
 * `inserted`, placed at `at` in `base`, carried over onto `saved`: the saved text
 * plus only that insertion, without whatever else differs between `base` and `saved`
 * (e.g. the user's unsaved typing). The insertion point maps onto `saved` when it
 * lies in the part both texts share before or after their differing span; null when
 * it falls inside that span (then no position in `saved` is the right one).
 */
export function rebaseInsertion(saved: string, base: string, at: number, inserted: string): string | null {
	const { at: diffStart, removed } = textSplice(base, saved);
	const sharedTail = base.length - diffStart - removed.length;
	let pos: number;
	if (at <= diffStart) pos = at;
	else if (base.length - at <= sharedTail) pos = saved.length - (base.length - at);
	else return null;
	return saved.slice(0, pos) + inserted + saved.slice(pos);
}
