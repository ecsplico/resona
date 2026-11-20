import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockNuxtImport } from "@nuxt/test-utils/runtime";
import fixture from "#shared/dictationEdit.cases.json";
import { createDirectusStore } from "~/data/directusStore";
import { useTranscriptEdit } from "~/composables/useTranscriptEdit";
import { renamePatch, reviewStatusPatch } from "~/utils/dictationEdit";
import { editedSinceProfile, postprocessRequest, postprocessRunning, rawRestore } from "~/utils/postprocess";
import type { Transcript } from "~/types/directus";

// The same cases pin the dictations MCP server (mcp/tests/test_edit_tools.py):
// both sides must send Directus exactly these writes.

type Write = { op: string; collection: string; id?: string; data?: Record<string, unknown> };

const state = vi.hoisted(() => ({ request: vi.fn() }));

mockNuxtImport("useDirectus", () => () => ({
	client: { request: state.request },
	assetUrl: (id: string) => `http://d/assets/${id}`,
}));

mockNuxtImport("useDataStore", () => () => createDirectusStore());

vi.mock("@directus/sdk", () => ({
	uploadFiles: (data: unknown) => ({ kind: "upload", data }),
	createItem: (collection: string, payload: unknown) => ({ op: "create", collection, data: payload }),
	readItems: (collection: string, query: unknown) => ({ op: "read", collection, query }),
	readItem: (collection: string, id: string) => ({ op: "read", collection, id }),
	updateItem: (collection: string, id: string, patch: unknown) => ({ op: "update", collection, id, data: patch }),
	deleteItem: (collection: string, id: string) => ({ op: "delete", collection, id }),
}));

const ids: Record<string, string> = { recording: fixture.recording, transcript: fixture.transcript };

describe("dictation edits (shared/dictationEdit.cases.json)", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		state.request.mockResolvedValue({ id: "new-row" });
	});

	for (const c of fixture.cases.filter(c => !("mcp_only" in c && c.mcp_only))) {
		it(c.name, async () => {
			const input = c.input as Record<string, unknown>;
			const transcript = ("transcript" in c ? c.transcript : null) as Pick<
				Transcript, "text" | "raw_text" | "processed_text" | "applied_profile" | "postprocess_status"
			> | null;
			const store = createDirectusStore();
			// The postprocess panel's guards: locked while a profile runs, asks before
			// replacing edits made since the profile ran (`confirm: true` = the user agreed).
			const edited = transcript !== null && editedSinceProfile(transcript, transcript.text ?? "");
			if ("error" in c) {
				// The app never gets there: its guard stops it before any write.
				if (c.error === "busy") expect(postprocessRunning(transcript)).toBe(true);
				else if (c.error === "confirm_required") expect(edited && input.confirm !== true).toBe(true);
				else if (c.error === "nothing_to_restore") expect(transcript?.applied_profile ?? null).toBeNull();
				else throw new Error(`no app guard for ${c.error}`);
				expect(c.writes).toEqual([]);
				return;
			}
			if (c.action === "postprocess" || c.action === "restore_raw") {
				expect(transcript).not.toBeNull();
				expect(postprocessRunning(transcript)).toBe(false);
				expect(!edited || input.confirm === true).toBe(true);
			}
			if (c.action === "update_text") {
				expect(postprocessRunning(transcript)).toBe(false);
				const edit = useTranscriptEdit(transcript?.text ?? "");
				const text = String(input.text);
				const ok = transcript
					? await edit.saveEdit(fixture.transcript, text)
					: (await edit.createTranscript(fixture.recording, text)) !== null;
				expect(ok).toBe(true);
			}
			else if (c.action === "rename") {
				await store.updateRecording(fixture.recording, renamePatch(String(input.title)));
			}
			else if (c.action === "set_review_status") {
				await store.updateRecording(fixture.recording, reviewStatusPatch(input.status as "editing" | "ready"));
			}
			else if (c.action === "delete") {
				await store.deleteRecording(fixture.recording);
			}
			else if (c.action === "postprocess") {
				const instructions = typeof input.instructions === "string" ? input.instructions : null;
				await store.updateTranscript(fixture.transcript, postprocessRequest(String(input.profile), instructions));
			}
			else if (c.action === "restore_raw") {
				await store.updateTranscript(fixture.transcript, rawRestore(transcript!));
			}
			else {
				throw new Error(`unknown action ${c.action}`);
			}
			const writes = (c.writes as Write[]).map(w => ({ ...w, ...(w.id ? { id: ids[w.id] } : {}) }));
			expect(state.request.mock.calls.map(([req]) => req)).toEqual(writes);
		});
	}
});
