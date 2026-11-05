import { ref } from "vue";
import type { Ref } from "vue";

/**
 * Diagnostic trail for push-to-talk dictation. The PTT flow itself only ever
 * shows the user one generic German error string — this captures the real
 * detail (exception name/message, HTTP status, response body) behind each
 * attempt so the /debug screen can show what actually happened, without
 * changing the user-facing PTT behavior.
 *
 * Module singleton: entries survive navigating away from the recording page
 * so /debug can show the last real attempt.
 */
export type PttDiagStage = "mic" | "record" | "upload";

export interface PttDiagEntry {
	ts: number;
	stage: PttDiagStage;
	ok: boolean;
	detail: string;
}

const MAX_ENTRIES = 20;
const entries: Ref<PttDiagEntry[]> = ref([]);

function record(stage: PttDiagStage, ok: boolean, detail: string): void {
	entries.value = [{ ts: Date.now(), stage, ok, detail }, ...entries.value].slice(0, MAX_ENTRIES);
}

function clear(): void {
	entries.value = [];
}

export function usePttDiagnostics() {
	return { entries, record, clear };
}
