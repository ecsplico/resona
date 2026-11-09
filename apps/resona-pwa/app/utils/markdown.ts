/**
 * The Markdown a postprocess profile delivers (headings, paragraphs, lists), split
 * into blocks the detail page renders as text nodes: no HTML is ever injected, so
 * whatever the transcript contains stays inert. Inline markup and tables are shown
 * as written.
 */
export type MarkdownBlock
	= | { kind: "heading"; level: number; text: string }
		| { kind: "paragraph"; lines: string[] }
		| { kind: "list"; ordered: boolean; items: string[] };

const HEADING = /^(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;

export function markdownBlocks(source: string): MarkdownBlock[] {
	const blocks: MarkdownBlock[] = [];
	let open: MarkdownBlock | null = null;

	for (const line of source.replace(/\r\n?/g, "\n").split("\n")) {
		if (line.trim() === "") {
			open = null;
			continue;
		}
		const heading = HEADING.exec(line);
		if (heading) {
			blocks.push({ kind: "heading", level: heading[1]!.length, text: heading[2]! });
			open = null;
			continue;
		}
		const bullet = BULLET.exec(line);
		const numbered = bullet ? null : NUMBERED.exec(line);
		const item = bullet ?? numbered;
		if (item) {
			const ordered = numbered !== null;
			if (open?.kind !== "list" || open.ordered !== ordered) {
				open = { kind: "list", ordered, items: [] };
				blocks.push(open);
			}
			open.items.push(item[1]!.trim());
			continue;
		}
		if (open?.kind === "list" && /^\s/.test(line)) {
			// Indented continuation of the last list item.
			const last = open.items.length - 1;
			open.items[last] = `${open.items[last]} ${line.trim()}`;
			continue;
		}
		if (open?.kind !== "paragraph") {
			open = { kind: "paragraph", lines: [] };
			blocks.push(open);
		}
		open.lines.push(line.trim());
	}
	return blocks;
}
