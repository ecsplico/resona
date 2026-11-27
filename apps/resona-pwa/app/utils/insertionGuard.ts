/**
 * Insertion guard for text that arrives late (a transcript, a correction, a title):
 * the target is snapshotted when the request starts and the result is resolved
 * against what the user sees when it arrives. An edit made meanwhile is never
 * overwritten: the insertion point is carried through it, or the result is dropped
 * when the point fell inside text the user replaced, or the target belongs to
 * someone else now (another recording, a new session).
 *
 * Port of T3 Code's `mapPosition` / `resolveTranscriptCommit` (its client-runtime
 * voice-input controller), framework-free.
 */

export interface TextSelectionRange {
	start: number;
	end: number;
}

export interface InsertionTarget {
	/** Who owns the text (a recording id, a live session); another owner never takes the result. */
	ownerKey: string;
	text: string;
	selection: TextSelectionRange;
	/** Optional change counter: a different revision counts as changed even when the text is equal again. */
	revision?: number;
}

export type InsertionSnapshot = Readonly<InsertionTarget>;

export type InsertionResult
	= | { kind: "insert"; text: string; selection: TextSelectionRange }
		| { kind: "dropped"; reason: "owner-changed" | "draft-changed-unmappable" };

export interface ResolveInsertionOptions {
	/** Language of the result (`de`, `en-US`, `auto`); decides the word spacing. Default `de`. */
	language?: string;
	/**
	 * `map` (default): an edit made meanwhile is kept and the result goes to the
	 * mapped place. `drop`: any change drops it (a whole-field suggestion such as a
	 * title must never mix with what the user typed).
	 */
	onChange?: "map" | "drop";
}

export interface MapPositionOptions {
	/** Which side to align first when the edit is ambiguous over repeated text. Default `prefix`. */
	alignment?: "prefix" | "suffix";
	/**
	 * Where a position at the start of the changed span goes: `after` (default)
	 * behind anything inserted there, `before` in front of it.
	 */
	bias?: "before" | "after";
}

// Languages written without spaces between words.
const WORD_SPACELESS_LANGUAGES: Record<string, true> = { ja: true, zh: true, yue: true, th: true, lo: true, km: true, my: true };

const isHighSurrogate = (code: number): boolean => code >= 0xd800 && code <= 0xdbff;
const isLowSurrogate = (code: number): boolean => code >= 0xdc00 && code <= 0xdfff;

/**
 * Where `position` in `before` lands in `after`. The edit between the two is taken
 * as one changed span (common prefix and suffix, per UTF-16 unit, never splitting
 * a surrogate pair). A position before or after that span moves with it; one at
 * the span's start goes behind the new text (`bias: "after"`) or stays in front of
 * it (`"before"`); one strictly inside text that was replaced has no place: null.
 */
export function mapPosition(before: string, after: string, position: number, options: MapPositionOptions = {}): number | null {
	const shared = Math.min(before.length, after.length);
	const matchesFromEnd = (offset: number): boolean =>
		before[before.length - 1 - offset] === after[after.length - 1 - offset];
	let prefix = 0;
	let suffix = 0;
	const scanSuffix = (limit: number): void => {
		while (suffix < limit && matchesFromEnd(suffix)) suffix += 1;
		if (suffix > 0 && isLowSurrogate(before.charCodeAt(before.length - suffix))) suffix -= 1;
	};
	const suffixFirst = options.alignment === "suffix";
	if (suffixFirst) scanSuffix(shared);
	while (prefix < shared - suffix && before[prefix] === after[prefix]) prefix += 1;
	if (prefix > 0 && isHighSurrogate(before.charCodeAt(prefix - 1))) prefix -= 1;
	if (!suffixFirst) scanSuffix(shared - prefix);

	const removedEnd = before.length - suffix;
	const delta = after.length - before.length;
	if (position < prefix) return position;
	if (position === prefix && options.bias === "before") return position;
	if (position >= removedEnd) return position + delta;
	if (position === prefix) return after.length - suffix;
	return null;
}

/**
 * `text` with the spaces it needs to sit between `left` and `right` (the
 * characters around the insertion point): a space between words, none before
 * punctuation or after an opening bracket, none in languages written without
 * spaces. `auto` (unknown language) only spaces Latin script and digits.
 * T3's rules, plus: a word inserted right before closing punctuation, or right
 * after an opening bracket, still gets its space on the other side.
 */
export function spaceInsertion(left: string, right: string, text: string, language = "de"): string {
	const lang = (language.split(/[-_]/)[0] ?? "").toLowerCase();
	if (WORD_SPACELESS_LANGUAGES[lang] === true) return text;
	const auto = lang === "auto";
	const l = left.slice(-2);
	const r = right.slice(0, 2);
	const leftEdge = auto ? /[A-Za-z0-9.!?,:;)\]}'"]$/ : /(?:\p{L}|\p{M}|\p{N}|[.!?,:;)\]}'"])$/u;
	const rightEdge = auto ? /^[A-Za-z0-9([{'"]/ : /^[\p{L}\p{M}\p{N}([{'"]/u;
	const leftNeedsBoundary = (!auto || /^[\p{Script=Latin}\p{N}]/u.test(text))
		&& leftEdge.test(l)
		&& (!r || /^[\s.,;:!?)\]}]/.test(r));
	const rightNeedsBoundary = (!auto || /(?:\p{Script=Latin}|\p{N})$/u.test(text))
		&& rightEdge.test(r)
		&& (!l || /[\s([{]$/.test(l));
	return `${leftNeedsBoundary ? " " : ""}${text}${rightNeedsBoundary ? " " : ""}`;
}

/** The target as it is now, taken when the request starts (a copy: later edits do not reach it). */
export function snapshotInsertion(target: InsertionTarget): InsertionSnapshot {
	return Object.freeze({
		ownerKey: target.ownerKey,
		text: target.text,
		selection: Object.freeze({ start: target.selection.start, end: target.selection.end }),
		...(target.revision === undefined ? {} : { revision: target.revision }),
	});
}

function clamp(value: number, max: number): number {
	return Math.max(0, Math.min(max, value));
}

/**
 * Where `transcript` goes now. Unchanged target: it replaces the snapshot's
 * selection (a caret gets word spacing). Changed target (`onChange: "map"`): it
 * goes, as a caret insertion, behind the snapshot's selection end carried through
 * the edit; the edit is ambiguous over repeated text, so the later of both
 * alignments wins, and it is dropped when neither maps. A missing target or
 * another owner drops it. An empty transcript leaves the text as it is.
 */
export function resolveInsertion(
	snapshot: InsertionSnapshot,
	current: InsertionTarget | null,
	transcript: string,
	options: ResolveInsertionOptions = {},
): InsertionResult {
	if (!current || current.ownerKey !== snapshot.ownerKey) {
		return { kind: "dropped", reason: "owner-changed" };
	}
	const { text } = current;
	const unchanged = text === snapshot.text && current.revision === snapshot.revision;
	if (!unchanged && options.onChange === "drop") {
		return { kind: "dropped", reason: "draft-changed-unmappable" };
	}
	const replacement = transcript.trim();
	if (!replacement) {
		return { kind: "insert", text, selection: { start: clamp(current.selection.start, text.length), end: clamp(current.selection.end, text.length) } };
	}

	let start: number;
	let end: number;
	if (unchanged) {
		start = clamp(Math.min(snapshot.selection.start, snapshot.selection.end), text.length);
		end = clamp(Math.max(snapshot.selection.start, snapshot.selection.end), text.length);
	}
	else {
		const from = Math.max(snapshot.selection.start, snapshot.selection.end);
		const mapped = [
			mapPosition(snapshot.text, text, from, { alignment: "prefix" }),
			mapPosition(snapshot.text, text, from, { alignment: "suffix" }),
		].filter((p): p is number => p !== null);
		if (mapped.length === 0) return { kind: "dropped", reason: "draft-changed-unmappable" };
		end = clamp(Math.max(...mapped), text.length);
		start = end;
	}

	const insertion = start === end
		? spaceInsertion(text.slice(0, start), text.slice(start), replacement, options.language)
		: replacement;
	const cursor = start + insertion.length;
	return {
		kind: "insert",
		text: text.slice(0, start) + insertion + text.slice(end),
		selection: { start: cursor, end: cursor },
	};
}

/** Short German notice for a dropped result; `what` names it ("Der Titelvorschlag"). */
export function droppedInsertionMessage(what: string, reason: "owner-changed" | "draft-changed-unmappable"): string {
	return reason === "owner-changed"
		? `${what} kam zu spät und wurde verworfen.`
		: `${what} wurde verworfen, weil sich der Text inzwischen geändert hat.`;
}
