/**
 * Deterministic recording title from a transcript (no model, no network).
 * Reference behaviour: shared/titleSuggestion.cases.json. The Python worker
 * mirrors this rule in worker/src/resona_project_worker/title.py; change both
 * together and keep the fixture green on both sides.
 */

const FILLERS = new Set(
	"äh ähm ääh öhm ehm hm hmm mhm halt eben mal sozusagen quasi okay ok".split(" "),
);

const STOPWORDS = new Set(
	("also ja nein so gut dann und oder aber jetzt nun ich wir du sie es man der die das den dem des "
		+ "ein eine einen einem einer eines zu zum zur von vom mit für auf in im an am bei beim ist sind "
		+ "war hat habe haben noch schon bitte danke").split(" "),
);

const MAX_TOKENS = 6;
const MAX_LENGTH = 60;
const MD_PREFIX = /^\s*(#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)/;
const MD_LINK = /\[([^\]]*)\]\([^)]*\)/g;
const SEGMENT_SPLIT = /[.!?;:\n]+/;
const TOKEN = /[\p{L}\p{N}]+(?:[-'’][\p{L}\p{N}]+)*/gu;

function stripStopwords(tokens: string[]): string[] {
	let start = 0;
	let end = tokens.length;
	while (start < end && STOPWORDS.has(tokens[start]!.toLowerCase())) {
		start++;
	}
	while (end > start && STOPWORDS.has(tokens[end - 1]!.toLowerCase())) {
		end--;
	}
	return tokens.slice(start, end);
}

export function suggestTitle(text: string): string | null {
	const cleaned = text
		.split("\n")
		.map(line => line.replace(MD_PREFIX, "").replace(MD_LINK, "$1").replace(/[*_`]/g, ""))
		.join("\n");

	for (const segment of cleaned.split(SEGMENT_SPLIT)) {
		const tokens = stripStopwords(
			(segment.match(TOKEN) ?? []).filter(t => !FILLERS.has(t.toLowerCase())),
		);
		if (!tokens.some(t => t.length >= 3)) {
			continue;
		}

		let picked = stripStopwords(tokens.slice(0, MAX_TOKENS));
		let title = picked.join(" ");
		while (title.length > MAX_LENGTH && picked.length > 1) {
			picked = stripStopwords(picked.slice(0, -1));
			title = picked.join(" ");
		}
		title = title.slice(0, MAX_LENGTH);
		if (!title) {
			continue;
		}
		return title.charAt(0).toUpperCase() + title.slice(1);
	}
	return null;
}

/** Date-based stand-in title, `Aufnahme DD.MM.YYYY HH:MM` in local time. */
export function placeholderTitle(date: Date): string {
	const p = (n: number): string => String(n).padStart(2, "0");
	return `Aufnahme ${p(date.getDate())}.${p(date.getMonth() + 1)}.${date.getFullYear()} ${p(date.getHours())}:${p(date.getMinutes())}`;
}
