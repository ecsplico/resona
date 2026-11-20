import type { RecordingPatch } from "~/data/types";

/**
 * The recording fields the detail page writes for a rename and a review status
 * change. The dictations MCP server (`mcp/src/resona_dictations_mcp/edit_tools.py`)
 * writes the same; `shared/dictationEdit.cases.json` pins both sides.
 */

/** A rename: the trimmed title, which from now on is the user's own (no placeholder). */
export function renamePatch(title: string): RecordingPatch {
	return { title: title.trim(), title_auto: false };
}

export function reviewStatusPatch(status: "editing" | "ready"): RecordingPatch {
	return { review_status: status };
}
