/**
 * On-device refinement of a live title suggestion with the browser's built-in
 * language model (Prompt API, `LanguageModel`; desktop Chrome). Only a model
 * that is ready right now is used: `create()` is never called in any other
 * state, so this never starts a model download. No network, no model shipped
 * by the app. Any failure, abort or unusable answer returns `null`, and the
 * caller keeps the deterministic `suggestTitle()` result.
 *
 * The model runs outside the page's main thread; callers only await promises.
 * The API is not exposed in Web Workers, so this runs on the page.
 */

export type TitleModelAvailability = "unsupported" | "unavailable" | "downloadable" | "downloading" | "available";

interface LanguageModelSession {
	prompt(input: string, options?: { signal?: AbortSignal }): Promise<string>;
	destroy(): void;
}

interface LanguageModelOptions {
	expectedInputs: { type: "text"; languages: string[] }[];
	expectedOutputs: { type: "text"; languages: string[] }[];
	initialPrompts?: { role: "system"; content: string }[];
	signal?: AbortSignal;
}

interface LanguageModelApi {
	availability(options: LanguageModelOptions): Promise<string>;
	create(options: LanguageModelOptions): Promise<LanguageModelSession>;
}

/** Longest accepted title, in characters and words (the heuristic allows 60/6). */
export const REFINED_MAX_CHARS = 60;
export const REFINED_MAX_WORDS = 8;
/** Transcript characters handed to the model; the start of a dictation names its subject. */
const PROMPT_MAX_CHARS = 2000;

const SYSTEM_PROMPT = "Du formulierst Titel für ärztliche Diktate. Antworte ausschließlich mit einem kurzen deutschen Titel "
	+ "aus zwei bis sechs Wörtern, höchstens 60 Zeichen, ohne Anführungszeichen, ohne Satzzeichen am Ende, ohne Erklärung. "
	+ "Beispiel: Knie-TEP Kontrolle rechts";

// English function words that never belong in a German title.
const ENGLISH_WORDS = new Set("the of and with for from after before about this that is are was your".split(" "));

function languageModel(): LanguageModelApi | null {
	const lm: unknown = Reflect.get(globalThis, "LanguageModel");
	if (!lm || (typeof lm !== "object" && typeof lm !== "function")) {
		return null;
	}
	// Shape checked at runtime: both methods must exist before the API is used.
	const api = lm as Partial<LanguageModelApi>;
	return typeof api.availability === "function" && typeof api.create === "function" ? api as LanguageModelApi : null;
}

function modelOptions(language: string): LanguageModelOptions {
	return {
		expectedInputs: [{ type: "text", languages: [language] }],
		expectedOutputs: [{ type: "text", languages: [language] }],
	};
}

/** Refinement is German-only: the prompt and the language check are German. */
export function refinementLanguageSupported(language: string): boolean {
	return language === "de";
}

/** State of the built-in model for `language`; asking never downloads anything. */
export async function titleModelAvailability(language: string): Promise<TitleModelAvailability> {
	const lm = languageModel();
	if (!lm || !refinementLanguageSupported(language)) {
		return "unsupported";
	}
	try {
		const state = await lm.availability(modelOptions(language));
		return state === "available" || state === "downloadable" || state === "downloading" ? state : "unavailable";
	}
	catch {
		return "unavailable";
	}
}

/**
 * Cleans a model answer into a title, or `null` when it is unusable: empty,
 * several lines, too long, cut off, or not German.
 */
export function acceptRefinedTitle(raw: string): string | null {
	const lines = raw.trim().split(/\n+/).filter(l => l.trim());
	if (lines.length !== 1) {
		return null;
	}
	const title = lines[0]!
		.trim()
		.replace(/^(\*\*)?titel(\*\*)?\s*:\s*/i, "")
		.replace(/^["'„“”‚‘’«»*]+|["'„“”‚‘’«»*]+$/g, "")
		.replace(/[.!]+$/, "")
		.trim();
	const words = title.split(/\s+/).filter(Boolean);
	if (!title || title.length > REFINED_MAX_CHARS || words.length > REFINED_MAX_WORDS) {
		return null;
	}
	// Cut off: trailing ellipsis/comma/dash/colon, or an unclosed bracket.
	if (/(\.\.\.|…|[,;:(\-–])$/.test(title) || (title.match(/\(/g)?.length ?? 0) !== (title.match(/\)/g)?.length ?? 0)) {
		return null;
	}
	if (!/\p{L}/u.test(title) || /[^\p{Script=Latin}\p{N}\p{P}\p{Zs}\p{M}+&/°%]/u.test(title)) {
		return null;
	}
	if (words.some(w => ENGLISH_WORDS.has(w.toLowerCase().replace(/[^\p{L}]/gu, "")))) {
		return null;
	}
	return title.charAt(0).toUpperCase() + title.slice(1);
}

/**
 * One refinement of `text` into a title. `null` unless the model is available
 * right now and answers with a usable title before `signal` aborts.
 */
export async function refineTitle(text: string, opts: { language: string; signal: AbortSignal }): Promise<string | null> {
	const lm = languageModel();
	const input = text.trim().slice(0, PROMPT_MAX_CHARS);
	if (!lm || !input || opts.signal.aborted) {
		return null;
	}
	if (await titleModelAvailability(opts.language) !== "available" || opts.signal.aborted) {
		return null;
	}
	let session: LanguageModelSession | null = null;
	try {
		session = await lm.create({
			...modelOptions(opts.language),
			initialPrompts: [{ role: "system", content: SYSTEM_PROMPT }],
			signal: opts.signal,
		});
		if (opts.signal.aborted) {
			return null;
		}
		const answer = await session.prompt(`Diktat:\n${input}\n\nTitel:`, { signal: opts.signal });
		return opts.signal.aborted ? null : acceptRefinedTitle(answer);
	}
	catch {
		return null;
	}
	finally {
		session?.destroy();
	}
}
