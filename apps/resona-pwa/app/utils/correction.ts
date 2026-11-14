import type { CorrectionEdit } from "~/types/directus";

// Above this many token pairs the LCS table gets too big for a phone; the
// changed middle is then reported as one single edit.
const MAX_LCS_CELLS = 4_000_000;

// A paragraph that left its place and one that appeared elsewhere count as one
// moved paragraph when at least this share of the longer one's words is shared.
const MOVED_MIN_SHARED = 0.5;

function tokens(text: string): string[] {
	return text.split(/(\s+)/).filter(part => part.length > 0);
}

/** Maps strings to integer ids, so the quadratic LCS loops compare numbers, not strings. */
function interner(): (value: string) => number {
	const ids = new Map<string, number>();
	return (value) => {
		let found = ids.get(value);
		if (found === undefined) {
			found = ids.size;
			ids.set(value, found);
		}
		return found;
	};
}

/** LCS lengths of all suffix pairs: cell `i * (b.length + 1) + j` is the LCS of `a[i..]` and `b[j..]`. */
function lcsTable(a: Int32Array, b: Int32Array): Uint16Array {
	const cols = b.length + 1;
	// The cell cap bounds the shorter side to 2000 entries, so an LCS length fits 16 bits.
	const table = new Uint16Array((a.length + 1) * cols);
	for (let i = a.length - 1; i >= 0; i -= 1) {
		const row = i * cols;
		const next = row + cols;
		const itemA = a[i];
		for (let j = b.length - 1; j >= 0; j -= 1) {
			table[row + j] = itemA === b[j]
				? table[next + j + 1]! + 1
				: Math.max(table[next + j]!, table[row + j + 1]!);
		}
	}
	return table;
}

/** Share of the longer paragraph's words that the two paragraphs have in common, in order. */
function sharedWords(x: string, y: string): number {
	const wx = x.split(/\s+/).filter(Boolean);
	const wy = y.split(/\s+/).filter(Boolean);
	if (wx.length === 0 || wy.length === 0 || wx.length * wy.length > MAX_LCS_CELLS) return 0;
	const idOf = interner();
	return lcsTable(Int32Array.from(wx, idOf), Int32Array.from(wy, idOf))[0]! / Math.max(wx.length, wy.length);
}

/**
 * `after` with every moved paragraph (one line) put back at the place it had in
 * `before`, so moving a paragraph is no correction while the words changed in it
 * still are. A paragraph only in `before` and one only in `after` are the same
 * moved paragraph when their trimmed text is equal or they share enough words.
 */
function unmoveParagraphs(before: string, after: string): string {
	const a = before.split("\n");
	const b = after.split("\n");
	if (a.length * b.length > MAX_LCS_CELLS) return after;
	const idOf = interner();
	const idA = Int32Array.from(a, idOf);
	const idB = Int32Array.from(b, idOf);
	const table = lcsTable(idA, idB);
	const cols = b.length + 1;
	// Line alignment as [index in a, index in b]; -1 = the line is only on the other side.
	const ops: [number, number][] = [];
	let i = 0;
	let j = 0;
	while (i < a.length || j < b.length) {
		if (i < a.length && j < b.length && idA[i] === idB[j]) ops.push([i++, j++]);
		else if (j < b.length && (i === a.length || table[i * cols + j + 1]! >= table[(i + 1) * cols + j]!)) ops.push([-1, j++]);
		else ops.push([i++, -1]);
	}
	const gone = ops.filter(([x, y]) => y < 0 && a[x]!.trim() !== "").map(([x]) => x);
	const added = ops.filter(([x, y]) => x < 0 && b[y]!.trim() !== "").map(([, y]) => y);
	if (gone.length === 0 || added.length === 0) return after;
	const partner = new Map<number, number>();
	const taken = new Set<number>();
	// Unchanged moves first, so a similar paragraph never takes an exact one's partner.
	for (const x of gone) {
		const match = added.find(y => !taken.has(y) && b[y]!.trim() === a[x]!.trim());
		if (match === undefined) continue;
		partner.set(x, match);
		taken.add(match);
	}
	for (const x of gone) {
		if (partner.has(x)) continue;
		let best = -1;
		let bestShare = MOVED_MIN_SHARED;
		for (const y of added) {
			if (taken.has(y)) continue;
			const share = sharedWords(a[x]!, b[y]!);
			if (share >= bestShare && (best < 0 || share > bestShare)) {
				best = y;
				bestShare = share;
			}
		}
		if (best < 0) continue;
		partner.set(x, best);
		taken.add(best);
	}
	if (partner.size === 0) return after;
	const out: string[] = [];
	for (const [x, y] of ops) {
		if (y < 0) {
			// A moved paragraph goes where it stood in `before`, in its `after` wording.
			const moved = partner.get(x);
			if (moved !== undefined) out.push(b[moved]!);
		}
		else if (x >= 0 || !taken.has(y)) out.push(b[y]!);
	}
	return out.join("\n");
}

/**
 * Changed spans between the engine/previous text and the saved text, on word level:
 * paragraphs the user only moved are put back first, shared prefix and suffix are
 * trimmed, the middle is aligned by LCS, adjacent differences are merged. Empty
 * when nothing changed.
 */
export function diffEdits(before: string, after: string): CorrectionEdit[] {
	if (before === after) return [];
	return wordEdits(before, unmoveParagraphs(before, after));
}

function wordEdits(before: string, after: string): CorrectionEdit[] {
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
	const idOf = interner();
	const idA = Int32Array.from(midA, idOf);
	const idB = Int32Array.from(midB, idOf);
	const cols = midB.length + 1;
	const table = lcsTable(idA, idB);
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
 * Offset `at` in `base`, carried over onto `saved`: it maps when it lies in the
 * part both texts share before or after their differing span; null when it falls
 * inside that span (then no position in `saved` is the right one).
 */
export function rebaseOffset(saved: string, base: string, at: number): number | null {
	const { at: diffStart, removed } = textSplice(base, saved);
	const sharedTail = base.length - diffStart - removed.length;
	if (at <= diffStart) return at;
	if (base.length - at <= sharedTail) return saved.length - (base.length - at);
	return null;
}

/**
 * `inserted`, placed at `at` in `base`, carried over onto `saved`: the saved text
 * plus only that insertion, without whatever else differs between `base` and `saved`
 * (e.g. the user's unsaved typing); null when `at` does not map (see `rebaseOffset`).
 */
export function rebaseInsertion(saved: string, base: string, at: number, inserted: string): string | null {
	const pos = rebaseOffset(saved, base, at);
	return pos === null ? null : saved.slice(0, pos) + inserted + saved.slice(pos);
}

/** Hex SHA-256 of `text` (UTF-8): identifies the saved text a part's anchor refers to. */
export async function sha256Hex(text: string): Promise<string> {
	const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
	return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, "0")).join("");
}
