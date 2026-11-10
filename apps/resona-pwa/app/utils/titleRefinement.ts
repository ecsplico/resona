/**
 * On-device refinement of a live title suggestion with the browser's built-in
 * language model (Prompt API, `LanguageModel`; desktop Chrome). Only a model
 * that is ready right now is used: `refineTitle` never calls `create()` in any
 * other state, so it never starts a model download. The one download path is
 * `installTitleModel`, run only from the settings button. No network, no model
 * shipped by the app. Any failure, abort or unusable answer returns `null`, and
 * the caller keeps the deterministic `suggestTitle()` result.
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
	monitor?: (monitor: EventTarget) => void;
}

interface LanguageModelApi {
	availability(options: LanguageModelOptions): Promise<string>;
	create(options: LanguageModelOptions): Promise<LanguageModelSession>;
}

// Longest accepted title. The prompt asks for at most six words (like the
// heuristic); two words of slack keep a good but slightly wordy answer.
const REFINED_MAX_CHARS = 60;
const REFINED_MAX_WORDS = 8;
/** Transcript characters handed to the model; the start of a dictation names its subject. */
const PROMPT_MAX_CHARS = 2000;

const SYSTEM_PROMPT = "Du formulierst Titel für ärztliche Diktate. Antworte ausschließlich mit einem kurzen deutschen Titel "
	+ "aus zwei bis sechs Wörtern, höchstens 60 Zeichen, ohne Anführungszeichen, ohne Satzzeichen am Ende, ohne Erklärung. "
	+ "Beispiel: Knie-TEP Kontrolle rechts";

// Function words of English and the Romance languages that never occur in a
// German title (words shared with German, e.g. "des", "du", "per", are left out).
const FOREIGN_WORDS = new Set(
	("the of and with for from after before about this that is are was your "
		+ "le la les et pour avec el los las del con por il di della").split(" "),
);

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
function refinementLanguageSupported(language: string): boolean {
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
	const line = lines[0]!.trim().replace(/^["'„“”‚‘’«»*]+|["'„“”‚‘’«»*]+$/g, "");
	// Cut off: trailing ellipsis/comma/dash/colon, or an unclosed bracket.
	if (/(\.\.\.|…|[,;:(\-–])$/.test(line) || (line.match(/\(/g)?.length ?? 0) !== (line.match(/\)/g)?.length ?? 0)) {
		return null;
	}
	const title = line
		.replace(/^(\*\*)?titel(\*\*)?\s*:\s*/i, "")
		.replace(/[.!]+$/, "")
		.trim();
	const words = title.split(/\s+/).filter(Boolean);
	if (!title || title.length > REFINED_MAX_CHARS || words.length > REFINED_MAX_WORDS) {
		return null;
	}
	if (!/\p{L}/u.test(title) || /[^\p{Script=Latin}\p{N}\p{P}\p{Zs}\p{M}+&/°%]/u.test(title)) {
		return null;
	}
	if (words.some(w => FOREIGN_WORDS.has(w.toLowerCase().replace(/[^\p{L}]/gu, "")))) {
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

/**
 * Downloads the built-in model for `language` and resolves once it is ready;
 * rejects when the browser refuses, the download fails or is aborted.
 * `onProgress` gets the downloaded fraction (0–1). MUST be called
 * synchronously from a click: the browser allows the download only with user
 * activation, so `create()` runs before anything is awaited.
 */
export async function installTitleModel(language: string, onProgress: (loaded: number) => void): Promise<void> {
	const lm = languageModel();
	if (!lm || !refinementLanguageSupported(language)) {
		throw new Error("Prompt API für diese Sprache nicht verfügbar");
	}
	const session = await lm.create({
		...modelOptions(language),
		monitor(monitor) {
			monitor.addEventListener("downloadprogress", (event) => {
				if ("loaded" in event && typeof event.loaded === "number") {
					onProgress(event.loaded);
				}
			});
		},
	});
	session.destroy();
}
