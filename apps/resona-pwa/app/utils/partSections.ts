/**
 * Where the texts of later parts (re-dictations) stand in the transcript, so the
 * editor can draw a soft line above and below each. `blocks` are the editor's
 * top-level paragraphs in order; a section is found where its non-empty lines
 * equal consecutive non-empty paragraphs (whitespace at the ends ignored). Returns
 * the first and last paragraph index of each section found once; a part whose text
 * was edited since is no longer found and gets no lines.
 */
export function partSections(blocks: readonly string[], sections: readonly string[]): { start: number; end: number }[] {
	const filled = blocks.flatMap((text, index) => (text.trim() ? [{ text: text.trim(), index }] : []));
	const out: { start: number; end: number }[] = [];
	const taken = new Set<number>();
	for (const section of sections) {
		const lines = section.split("\n").map(line => line.trim()).filter(Boolean);
		if (lines.length === 0) continue;
		for (let i = 0; i + lines.length <= filled.length; i++) {
			if (lines.some((line, k) => taken.has(i + k) || filled[i + k]!.text !== line)) continue;
			for (let k = 0; k < lines.length; k++) taken.add(i + k);
			out.push({ start: filled[i]!.index, end: filled[i + lines.length - 1]!.index });
			break;
		}
	}
	return out;
}
