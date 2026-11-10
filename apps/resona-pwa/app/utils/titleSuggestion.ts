/**
 * Rule-based recording title from a transcript (issue #4): deterministic,
 * derived only from the text, no model and no network call.
 *
 * `deriveTitle` is THE rule: the first Markdown heading, else the first
 * sentence's (or clause's) first meaningful words; null when no usable word is
 * left. The project-worker ports it to Python
 * (`worker/src/resona_project_worker/title.py`); both are checked against
 * `shared/titleSuggestion.cases.json`, so change the rule, the port and the
 * fixture together.
 *
 * Around it: `suggestTitle` (live dictation) lets a calendar/`?title=` preset
 * win and falls back to "Diktat <dd.mm.yyyy HH:mm>"; recordings saved without
 * a title get `placeholderTitle` ("Aufnahme …") until the processor derives one.
 */

export const TITLE_MAX_CHARS = 60;
export const TITLE_MAX_WORDS = 6;
// A comma ends the title candidate only once this many words are collected.
const MIN_CLAUSE_WORDS = 3;
/** Meaningful words of final text before the live view shows a suggestion (~10–15 s of dictation). */
export const LIVE_SUGGESTION_MIN_WORDS = 20;
/** Meaningful words of captured text before a hub model is asked to refine the title after stop. */
export const TITLE_REFINE_MIN_WORDS = 6;

export interface TitleSuggestionOptions {
	calendarTitle?: string;
	date?: Date;
}

// Filler sounds and discourse particles that never belong in a title.
const FILLERS = new Set([
	"äh", "ähm", "öh", "öhm", "ehm", "hm", "hmm", "mhm", "äääh", "ääh", "ähh", "ähhm",
	"also", "ja", "so", "okay", "ok", "genau", "gut", "na", "nun", "naja",
]);

// Spoken dictation commands (see resona-postprocess `default_replacements.json`).
const COMMANDS = new Set([
	"komma", "punkt", "absatz", "doppelpunkt", "fragezeichen", "ausrufezeichen",
	"semikolon", "strichpunkt", "bindestrich", "kapitel", "klammer", "anführungszeichen",
]);

// Spoken commands that end a sentence.
const SENTENCE_COMMANDS = new Set(["punkt", "absatz", "fragezeichen", "ausrufezeichen", "doppelpunkt"]);

// Function words a title should neither start nor end with.
const STOPWORDS = new Set([
	"der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "eines",
	"und", "oder", "aber", "mit", "über", "von", "vom", "zu", "zum", "zur", "für", "auf", "an",
	"am", "im", "in", "bei", "beim", "nach", "aus", "um", "wie", "dass", "ist", "sind", "war",
	"hat", "haben", "wird", "werden", "es", "ich", "wir", "sie", "er", "heute", "jetzt", "dann",
	"seit", "wegen", "bis", "ohne", "gegen", "unter", "vor", "durch", "sehr", "noch", "auch",
]);

function pad(n: number): string {
	return String(n).padStart(2, "0");
}

function datedTitle(prefix: string, date: Date): string {
	const day = `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()}`;
	return `${prefix} ${day} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** Fallback for a live dictation without usable words, `Diktat DD.MM.YYYY HH:MM` (local time). */
export function fallbackTitle(date: Date = new Date()): string {
	return datedTitle("Diktat", date);
}

/** Stand-in for a recording saved without a title, `Aufnahme DD.MM.YYYY HH:MM` (local time). */
export function placeholderTitle(date: Date = new Date()): string {
	return datedTitle("Aufnahme", date);
}

function stripTrailingPunctuation(s: string): string {
	return s.replace(/[\s.,;:!?…\-–—"'„“”»«()[\]]+$/u, "");
}

function capLength(s: string): string {
	if (s.length <= TITLE_MAX_CHARS) {
		return s;
	}
	const cut = s.slice(0, TITLE_MAX_CHARS + 1);
	const space = cut.lastIndexOf(" ");
	return stripTrailingPunctuation(space > 0 ? cut.slice(0, space) : s.slice(0, TITLE_MAX_CHARS));
}

function finish(s: string): string {
	const cleaned = stripTrailingPunctuation(s.replace(/\s+/g, " ").trim());
	if (!cleaned) {
		return "";
	}
	const capped = capLength(cleaned);
	return capped.charAt(0).toLocaleUpperCase("de-DE") + capped.slice(1);
}

function bare(word: string): string {
	return word.toLocaleLowerCase("de-DE").replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");
}

function firstHeading(text: string): string {
	const m = /^\s{0,3}#{1,6}\s+(.+?)(?:\s+#+)?\s*$/m.exec(text);
	return m ? finish(m[1]!) : "";
}

/**
 * Words of the first sentence (or clause, once three words are in) without fillers and
 * dictation commands. A line break ends a sentence; list markers and Markdown links
 * (text kept) are dropped per line.
 */
function firstSentenceWords(text: string): string[] {
	const words: string[] = [];
	for (const line of text.split("\n")) {
		const tokens = line
			.replace(/^\s*(?:[-*+]|\d+[.)])\s+/, "")
			.replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
			.replace(/[#*_`>]+/g, " ")
			.split(/\s+/)
			.filter(Boolean);
		for (const token of tokens) {
			const key = bare(token);
			const endsSentence = /[.!?]["'”»)]*$/u.test(token) && !/^\d+[.)]$/.test(token);
			if (key && SENTENCE_COMMANDS.has(key)) {
				if (words.length > 0) return words;
				continue;
			}
			if (key === "komma" && words.length >= MIN_CLAUSE_WORDS) return words;
			if (key && !FILLERS.has(key) && !COMMANDS.has(key)) {
				words.push(token.replace(/[,;:]+$/u, ""));
			}
			if (endsSentence && words.length > 0) return words;
			if (/[,;:]$/u.test(token) && words.length >= MIN_CLAUSE_WORDS) return words;
		}
		if (words.length > 0) return words;
	}
	return words;
}

/** Number of words that are neither filler nor dictation command. */
export function meaningfulWordCount(text: string): number {
	let n = 0;
	for (const token of text.split(/\s+/)) {
		const key = bare(token);
		if (key && !FILLERS.has(key) && !COMMANDS.has(key)) n += 1;
	}
	return n;
}

/** The title rule shared with the project-worker: heading, else first-sentence words; null without usable words. */
export function deriveTitle(text: string): string | null {
	const heading = firstHeading(text);
	if (heading) {
		return heading;
	}
	let words = firstSentenceWords(text);
	while (words.length > 0 && STOPWORDS.has(bare(words[0]!))) words = words.slice(1);
	words = words.slice(0, TITLE_MAX_WORDS);
	while (words.length > 0 && STOPWORDS.has(bare(words.at(-1)!))) words = words.slice(0, -1);
	return finish(words.join(" ")) || null;
}

/** Live dictation title: calendar preset, else `deriveTitle`, else `fallbackTitle`. */
export function suggestTitle(text: string, opts: TitleSuggestionOptions = {}): string {
	return opts.calendarTitle?.trim() || deriveTitle(text) || fallbackTitle(opts.date);
}
