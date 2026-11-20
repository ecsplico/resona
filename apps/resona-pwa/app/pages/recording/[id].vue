<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { humanizeError, isServiceUnreachable } from "#shared/errors";
import { HubError } from "~/composables/useHub";
import type { RedictationAnchor, RedictationAudio } from "~/composables/useRedictation";
import type { Recording, RecordingPart, Transcript } from "~/types/directus";
import type { HMenuItem } from "~/components/hearth/HMenu.vue";
import { appendToRaw, postprocessRunning } from "~/utils/postprocess";
import { rebaseInsertion, rebaseOffset, sha256Hex } from "~/utils/correction";
import { renamePatch, reviewStatusPatch } from "~/utils/dictationEdit";

const route = useRoute();
const id = String(route.params.id);
const cfg = useRuntimeConfig().public;

const { recording, transcript, loading, error, refresh, startPolling, stopPolling } = useRecording(id);
const store = useDataStore();
// `wide`: paragraphs can be moved (desktop only; phones keep the plain editor).
const { paneInLayout, wide } = useShell();
// The desktop list stays mounted beside this page: ask it to reload after a change it shows.
const recordingsList = useRecordingsRefresh();

function formatDuration(seconds?: number): string {
	if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "";
	const total = Math.round(seconds);
	return `${Math.floor(total / 60)}:${(total % 60).toString().padStart(2, "0")}`;
}

function formatDate(iso?: string): string {
	if (!iso) return "";
	const date = new Date(iso);
	if (Number.isNaN(date.getTime())) return "";
	return date.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
}

const metaLine = computed(() => {
	const r = recording.value;
	if (!r) return "";
	return [
		formatDate(r.date_created),
		formatDuration(r.duration_seconds),
		r.language ? r.language.toUpperCase() : "",
		r.profile ?? "",
	].filter(part => part.length > 0).join(" · ");
});

const hubSettings = useHubSettings();
const provenanceLines = computed(() => describeProvenance(transcript.value?.provenance));

// Player source resolves asynchronously through the data store (Directus asset
// URL or a local object URL); refresh it only when the recording's audio
// actually changes (multi-source watch compares the primitives, so a poll
// returning the same audio_file does not re-resolve). Local `blob:` URLs are
// revoked when replaced and on unmount.
const audioSrc = ref<string | null>(null);
let audioDisposed = false;

function revokeAudio(url: string | null): void {
	if (url?.startsWith("blob:")) {
		URL.revokeObjectURL(url);
	}
}

watch([() => recording.value?.id, () => recording.value?.audio_file], async () => {
	const current = recording.value;
	const next = current ? await store.audioUrl(current) : null;
	if (audioDisposed) {
		revokeAudio(next);
		return;
	}
	revokeAudio(audioSrc.value);
	audioSrc.value = next;
}, { immediate: true });

// Where each later part (re-dictation) starts in the audio: faint ticks on the
// player's track. Read again whenever the audio file changes (a part was appended).
const partMarks = ref<number[]>([]);
// The texts later parts added to the transcript: the editor draws a soft line
// above and below each one it still finds. Parts landed on this page are added
// as they land.
const partTexts = ref<string[]>([]);

watch([() => recording.value?.id, () => recording.value?.audio_file], async ([recId, file]) => {
	if (!recId || !file) {
		partMarks.value = [];
		return;
	}
	try {
		const parts = await store.listParts(recId);
		partMarks.value = parts.flatMap(p => (p.offset_seconds != null && p.offset_seconds > 0 ? [p.offset_seconds] : []));
		const stored = parts.flatMap(p => ((p.status === "applied" || p.status === "done") && p.text ? [p.text] : []));
		partTexts.value = [...new Set([...partTexts.value, ...stored])];
	}
	catch {
		// Ticks and lines are an aid only: without the parts there are none.
		partMarks.value = [];
	}
}, { immediate: true });

// Distinguish a transient "service unreachable" failure (offline styling +
// retry hint) from a genuine transcription error (red banner).
const offlineError = computed(() => isServiceUnreachable(recording.value?.error_message));

const edit = useTranscriptEdit("");
const seeded = ref(false);

const { toText, copy, downloadText, downloadMarkdown } = useExport();

// Build a transcript snapshot that reflects the current (possibly edited) text
// so exports and copies match what the user sees.
function currentTranscript(): Transcript | null {
	if (!transcript.value) {
		return null;
	}
	return { ...transcript.value, text: edit.text.value };
}

// A recording is editable once transcription reaches a terminal state — whether
// or not a transcript exists yet (an errored or never-transcribed recording can
// still be typed/dictated into by hand).
const canEdit = computed(() =>
	recording.value?.status === "done" || recording.value?.status === "error",
);

// Typed edits (the editor's `typed` event: keyboard, paste, cut, undo) are
// counted; `savedTypedEdits` is the count the saved text covers. Dictation and
// continued parts never count: they are saved on their own as soon as they land
// (`autosavePart`), so they need no "Speichern".
const typedEdits = ref(0);
const savedTypedEdits = ref(0);
// A dictated or continued part whose own save did not go through (write failed,
// or it landed inside unsaved typing): it waits for "Speichern" like typed edits.
const unsavedPart = ref(false);
// New raw content (dictation, a continued part) added after a profile ran and not
// written yet: it goes into `text` and, on the next save, onto the end of
// `raw_text`, so a re-run or a restore keeps it.
let rawParts: string[] = [];

function forgetUnsaved(): void {
	savedTypedEdits.value = typedEdits.value;
	unsavedPart.value = false;
	rawParts = [];
}

// Seed the editable text once the transcript first appears, or — for a terminal
// recording that has no transcript — start from empty so the user can type or
// dictate. Polling stops once terminal, so a single seed is enough; guard
// against clobbering the user's edits on a later refresh.
watch([recording, transcript], ([, t]) => {
	if (seeded.value) {
		return;
	}
	if (t) {
		edit.seed(t.text ?? "");
		seeded.value = true;
		forgetUnsaved();
	}
	else if (canEdit.value) {
		edit.seed("");
		seeded.value = true;
		forgetUnsaved();
	}
}, { immediate: true });

// Unsaved: the editor differs from the saved text because of typing or a part
// whose own save failed. A dictation chip adds nothing to the text, and a part
// being saved right now is not unsaved either.
const dirty = computed(() =>
	seeded.value && edit.text.value !== edit.committed.value
	&& (typedEdits.value !== savedTypedEdits.value || unsavedPart.value));

function noteRawPart(text: string): void {
	if (transcript.value?.applied_profile && text.trim()) rawParts.push(text);
}

// Writes `next` as the transcript text, or creates the row with it when there is
// none. `parts`: raw content in `next` that `raw_text` lacks so far; `rawOnly`:
// nothing but such parts was added since the last save; `correction`: store the
// change as engine correction data.
async function writeText(next: string, parts: string[], rawOnly: boolean, correction: boolean): Promise<boolean> {
	const t = transcript.value;
	if (!t) {
		const created = await edit.createTranscript(id, next);
		if (created) transcript.value = created;
		return created !== null;
	}
	const raw = parts.length > 0 ? appendToRaw(t, parts) : null;
	// Only raw content was added to an unedited profile output: the new text is
	// still "the output plus raw parts", which a re-run or restore reproduces, so
	// it becomes the baseline and those do not ask.
	const keepsOutput = rawOnly && t.applied_profile != null && edit.committed.value === (t.processed_text ?? null);
	const ok = await edit.saveEdit(t.id, next, {
		// An edit of a profile's output is no correction: the engine did not write that text.
		correction: correction && !t.applied_profile
			? { recording: id, engine: t.engine, language: recording.value?.language }
			: undefined,
		extra: {
			...(raw === null ? {} : { raw_text: raw }),
			...(keepsOutput ? { processed_text: next } : {}),
		},
	});
	if (!ok) return false;
	t.text = next;
	if (raw !== null) t.raw_text = raw;
	if (keepsOutput) t.processed_text = next;
	void reanchorParts();
	return true;
}

/**
 * After a write the saved text changed: every chip still waiting gets its place
 * in the new saved text stored, so a reopen puts the part there. A chip inside
 * unsaved typing keeps its old anchor.
 */
async function reanchorParts(): Promise<void> {
	const offsets = editorRef.value?.dictationPartOffsets() ?? [];
	if (offsets.length === 0) return;
	const saved = edit.committed.value;
	const sha = await sha256Hex(saved);
	const anchors = new Map<string, RedictationAnchor>();
	for (const { partId, base, at } of offsets) {
		const offset = rebaseOffset(saved, base, at);
		if (offset !== null) anchors.set(partId, { anchor_offset: offset, anchor_text_sha: sha });
	}
	redict.reanchor(anchors);
}

// One manual save at a time: clicks while it is queued or running join it.
let manualSave: Promise<boolean> | null = null;

/** Persists the editor text; false when the save failed (the text stays in the editor). */
function saveTranscript(): Promise<boolean> {
	if (manualSave) return manualSave;
	// Taken at the click: the text the user asked to save. `enqueue` turns `saving`
	// on right away; the write waits for parts saving before it.
	const target = edit.text.value;
	const typedUpTo = typedEdits.value;
	manualSave = edit.enqueue(async () => {
		if (target !== edit.committed.value) {
			// Typed edits become correction data for the engine; a part whose own save
			// failed is new content, not a correction. Writes run one at a time, so no
			// part's save adds to `rawParts` meanwhile.
			const rawOnly = typedUpTo === savedTypedEdits.value;
			if (!await writeText(target, rawParts, rawOnly, !rawOnly)) return false;
			rawParts = [];
		}
		savedTypedEdits.value = typedUpTo;
		unsavedPart.value = false;
		redict.saved();
		return true;
	}).finally(() => {
		manualSave = null;
	});
	return manualSave;
}

/**
 * Saves a dictated or continued part on its own as soon as it lands: `place`
 * puts it into the saved text as it is when the write runs (null: no place is
 * right). The user's unsaved typing is never written along and stays unsaved. A
 * part that cannot be saved this way waits for "Speichern" like typed edits.
 */
function autosavePart(part: string, place: (saved: string) => string | null): Promise<boolean> {
	return edit.enqueue(async () => {
		const next = place(edit.committed.value);
		// New content, not a correction of the engine's text.
		const saved = next !== null && await writeText(next, [part], true, false);
		if (!saved) {
			unsavedPart.value = true;
			noteRawPart(part);
		}
		return saved;
	});
}

/** Throws the unsaved edits away and shows the last saved text again. */
function discardEdits(): void {
	edit.text.value = edit.committed.value;
	edit.error.value = null;
	forgetUnsaved();
}

// ── Nachbearbeiten (postprocess profile on the raw transcript) ────────────
// While a profile runs, the text is locked (editor, dictation, continue): the
// result replaces it as a whole. Once it lands, the editor shows it.
const postprocessing = computed(() => postprocessRunning(transcript.value));

// The text is read-only until "Bearbeiten" (pencil) turns editing on. A profile's
// output is shown rendered (headings, paragraphs) while not editing.
const editing = ref(false);
const showRendered = computed(() => Boolean(transcript.value?.applied_profile) && !editing.value);

// One header button: the pencil turns editing on; while editing (or with unsaved
// changes) save and discard (X) stand in its place, and both end editing.
async function startEditing(): Promise<void> {
	editing.value = true;
	await nextTick();
	editorRef.value?.focus();
}

async function saveAndClose(): Promise<void> {
	if (await saveTranscript()) editing.value = false;
}

function discardAndClose(): void {
	discardEdits();
	editing.value = false;
}

function reseed(): void {
	edit.seed(transcript.value?.text ?? "");
	forgetUnsaved();
	editing.value = false;
}

watch(postprocessing, (running, wasRunning) => {
	if (wasRunning && !running && transcript.value) reseed();
});

async function onPostprocessChanged(): Promise<void> {
	await refresh();
	// Restoring the raw transcript changes the text right away; a request only locks it.
	if (transcript.value && !postprocessing.value) reseed();
	startPolling();
}

// ── Title (editable on the detail page) ────────────────────────────────────
const editingTitle = ref(false);
const titleDraft = ref("");
const titleError = ref<string | null>(null);
const titleInput = ref<HTMLInputElement | null>(null);

async function startTitleEdit(): Promise<void> {
	if (!recording.value) return;
	titleDraft.value = recording.value.title ?? "";
	titleError.value = null;
	editingTitle.value = true;
	await nextTick();
	titleInput.value?.focus();
	titleInput.value?.select();
}

function cancelTitleEdit(): void {
	editingTitle.value = false;
	titleError.value = null;
}

async function commitTitle(): Promise<void> {
	if (!editingTitle.value || !recording.value) return;
	const next = titleDraft.value.trim();
	// Unchanged and already the user's own: nothing to write. Confirming an
	// unchanged automatic (placeholder) title still makes it the user's choice.
	if (next === (recording.value.title ?? "") && recording.value.title_auto !== true) {
		editingTitle.value = false;
		return;
	}
	try {
		await store.updateRecording(id, renamePatch(next));
		recording.value.title = next;
		recording.value.title_auto = false;
		recordingsList.request();
		editingTitle.value = false;
		titleError.value = null;
	}
	catch (err) {
		titleError.value = humanizeError(err, "Titel konnte nicht gespeichert werden.");
	}
}

// ── Review status (editing / ready) ────────────────────────────────────────
const reviewStatus = computed<Recording["review_status"]>(() => recording.value?.review_status ?? "editing");
const released = computed(() => reviewStatus.value === "ready");
const settingStatus = ref(false);

async function setReviewStatus(value: "editing" | "ready"): Promise<void> {
	if (!recording.value || reviewStatus.value === value) {
		return;
	}
	settingStatus.value = true;
	try {
		await store.updateRecording(id, reviewStatusPatch(value));
		recording.value.review_status = value;
	}
	finally {
		settingStatus.value = false;
	}
}

// ── Push-to-talk dictation (batch REST, a chip marks the insertion point) ──
type PttEditorRef = {
	appendParagraph: (text: string) => void;
	focus: () => void;
	hasFocus: () => boolean;
	insertDictationPart: (partId: string, at: "caret" | "end" | number, status?: "recording" | "transcribing" | "error") => void;
	dictationPartOffsets: () => { partId: string; base: string; at: number }[];
	setDictationPartStatus: (partId: string, status: "recording" | "transcribing" | "error") => void;
	resolveDictationPart: (partId: string, text: string, options?: { paragraph?: boolean }) => { base: string; at: number; inserted: string } | null;
	removeDictationPart: (partId: string) => void;
} | null;

const editorRef = ref<PttEditorRef>(null);
const pttState = ref<"idle" | "recording" | "transcribing">("idle");
const pttError = ref<string | null>(null);
const pttRecorder = useRecorder();
const pttDiag = usePttDiagnostics();
// The chip of the dictation in flight (null: none).
let pttPart: string | null = null;
let pttSeq = 0;

function dropPttPart(): void {
	if (pttPart) editorRef.value?.removeDictationPart(pttPart);
	pttPart = null;
}

/**
 * Puts the dictated text where its chip stands and saves only that text: the
 * chip's place in the plain text, carried over onto the saved text, so unsaved
 * typing around it stays unsaved. Without the chip (the editor was closed
 * meanwhile) the text goes to the end as a new paragraph.
 */
function landPttText(text: string): void {
	const partId = pttPart;
	pttPart = null;
	const placed = partId ? editorRef.value?.resolveDictationPart(partId, text) ?? null : null;
	if (!text) return;
	if (placed) {
		const { base, at, inserted } = placed;
		void autosavePart(text, saved => rebaseInsertion(saved, base, at, inserted));
		return;
	}
	appendParagraph(text);
	void autosavePart(text, current => joinParagraph(current, text));
}

function describeError(err: unknown): string {
	return err instanceof Error ? `${err.name}: ${err.message}` : String(err);
}

// Left Ctrl everywhere (code "ControlLeft"). The macOS fn/globe key can't be
// used — browsers never deliver a keydown/keyup for it. Label reads "ctrl" on
// Mac, "Strg" on Windows/Linux.
const isMac = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform);
const pttKeyCode = "ControlLeft";
const pttKeyLabel = isMac ? "ctrl" : "Strg";

async function startDictation(): Promise<void> {
	if (pttState.value !== "idle" || redict.recording.value) return;
	pttError.value = null;
	pttStopWanted = false;
	// Dictation inserts at the caret, so the rendered view gives way to the editor.
	if (showRendered.value) {
		editing.value = true;
		await nextTick();
	}
	pttPart = `ptt-${++pttSeq}`;
	// At the caret when the user was in the text at the press, else at its end.
	const ed = editorRef.value;
	ed?.insertDictationPart(pttPart, editingAtPress || ed.hasFocus() ? "caret" : "end");
	editingAtPress = false;
	try {
		await pttRecorder.start();
		pttState.value = "recording";
		pttDiag.record("mic", true, "Mikrofonzugriff erteilt.");
		// Released while the microphone was still opening.
		if (pttStopWanted) void stopDictation();
	}
	catch (err) {
		dropPttPart();
		pttDiag.record("mic", false, describeError(err));
		pttError.value = "Mikrofon nicht verfügbar.";
	}
}

async function stopDictation(): Promise<void> {
	pttStopWanted = false;
	if (pttState.value !== "recording") return;
	pttState.value = "transcribing";
	if (pttPart) editorRef.value?.setDictationPartStatus(pttPart, "transcribing");
	const blob = await pttRecorder.stop();
	if (!blob || blob.size === 0) {
		dropPttPart();
		pttDiag.record("record", false, "Leere Aufnahme (0 Bytes) — MediaRecorder hat keine Daten geliefert.");
		pttState.value = "idle";
		return;
	}
	const hub = useHub();
	if (!hub.hasToken()) {
		pttDiag.record("upload", false, "Kein Hub-Token – in Einstellungen → Verbindung eintragen");
		pttError.value = "Transkription fehlgeschlagen.";
		dropPttPart();
		pttState.value = "idle";
		return;
	}
	let recordedFailure = false;
	try {
		let rawText: string;
		try {
			rawText = await hub.transcribeSync(blob, "dictation.webm", {
				language: recording.value?.language ?? cfg.defaultLanguage,
			});
		}
		catch (err) {
			if (err instanceof HubError) {
				recordedFailure = true;
				pttDiag.record("upload", false, err.code === "timeout"
					? "Hub-Zeitüberschreitung (Job läuft weiter)"
					: `HTTP ${err.status} ${err.code} — ${err.message}`);
			}
			throw err;
		}
		pttDiag.record("upload", true, `${blob.size}B Audio → ${rawText.length} Zeichen`);
		const text = rawText.trim();
		landPttText(text);
	}
	catch (err) {
		if (!recordedFailure) {
			pttDiag.record("upload", false, describeError(err));
		}
		pttError.value = "Transkription fehlgeschlagen.";
		dropPttPart();
	}
	finally {
		pttState.value = "idle";
	}
}

// The record button: holding it (or the left Ctrl key) dictates at the caret while
// held, or at the end of the text when not editing. A release that comes while
// the microphone is still opening stops the dictation as soon as it records.
let pttStopWanted = false;

function releaseDictation(): void {
	if (pttState.value === "recording") void stopDictation();
	else if (pttPart) pttStopWanted = true;
}

function onKeyDown(e: KeyboardEvent): void {
	if (e.code === pttKeyCode && !e.repeat && pttState.value === "idle" && !redict.recording.value && !postprocessing.value) {
		e.preventDefault();
		void startDictation();
	}
}

function onKeyUp(e: KeyboardEvent): void {
	if (e.code === pttKeyCode && pttPart) {
		e.preventDefault();
		releaseDictation();
	}
}

const recordLabel = computed(() => {
	if (pttState.value === "recording") return "Diktat läuft – loslassen zum Einfügen";
	if (pttState.value === "transcribing") return "Diktat wird transkribiert…";
	return `Aufnehmen: tippen hängt an, halten (oder ${pttKeyLabel}) diktiert an der Cursorposition`;
});
// A profile run replaces the text as a whole: nothing is recorded into it meanwhile.
const canRecord = computed(() => canEdit.value && !postprocessing.value);

// ── Re-dictation (record more parts; each lands where its chip stands) ─────
// The bar records one part at a time; a stopped part is stored with its audio
// and transcribed by the processor (project worker / in-browser processor), so
// the next part, typing, saving and even closing the page never wait. Each part
// is a chip in the editor until its text arrives; opened again, the recording's
// unfinished parts come back as chips at their stored place.
const redict = useRedictation({
	recording: () => recording.value,
	anchor: anchorOf,
	onRestore: restoreRedictation,
	onText: landRedictation,
	onError: partId => editorRef.value?.setDictationPartStatus(partId, "error"),
	onAudio: applyRedictationAudio,
});
// Parts whose transcription failed: one banner each, with retry/discard.
const redictFailed = computed(() => redict.parts.value.filter(p => p.status === "error"));
// Parts whose audio could not be appended (blob only in memory): one banner each, with retry/discard.
const redictAudioFailed = computed(() => redict.parts.value.filter(p => p.audio === "failed"));
const redictNotice = ref<string | null>(null);
const redictProgress = computed(() => {
	const n = redict.transcribing.value;
	if (n > 0) return `${n === 1 ? "1 Teil wird" : `${n} Teile werden`} transkribiert.`;
	return redict.merging.value ? "Audio wird angehängt…" : null;
});
// A part not landed yet (recording, storing, transcribing, failed): its anchor
// points into the current text, so nothing may rewrite that text meanwhile.
const redictUnsettled = computed(() => redict.busy.value || redict.parts.value.length > 0);
// Why "Nachbearbeiten" cannot start now: it runs on the stored text, so unsaved
// edits, writes still running and dictation/re-dictation in progress must settle first.
const postprocessBlocked = computed(() => {
	if (dirty.value) return "Erst speichern oder verwerfen, dann nachbearbeiten.";
	if (edit.saving.value) return "Der Text wird noch gespeichert.";
	if (pttState.value !== "idle" || redictUnsettled.value) return "Erst die laufende Aufnahme abschließen.";
	return null;
});

// Moved-to-end notice for parts restored away from their anchor.
const MOVED_TO_END = "Ein Nachdiktat wurde ans Ende gesetzt, weil sich der Text geändert hat.";
// The part the record bar is recording (null: bar closed).
let redictMicPart: string | null = null;
// A press can move the focus out of the text, so whether the user was editing is
// noted when the record button goes down, before that could happen.
let editingAtPress = false;

function noteRecordPress(): void {
	editingAtPress = editorRef.value?.hasFocus() ?? false;
}

async function startRedictation(): Promise<void> {
	if (redict.recording.value || pttState.value !== "idle") return;
	redictNotice.value = null;
	// Also the part's row id, so the chip and the stored part are one.
	const partId = crypto.randomUUID();
	// Editing (caret in the text): the part splits the paragraph at the caret;
	// otherwise it is appended at the end. The rendered profile output has no
	// editor: the text is appended when it arrives.
	const ed = showRendered.value ? null : editorRef.value;
	ed?.insertDictationPart(partId, editingAtPress || ed.hasFocus() ? "caret" : "end");
	editingAtPress = false;
	redictMicPart = partId;
	if (!await redict.start(partId)) {
		editorRef.value?.removeDictationPart(partId);
		if (redictMicPart === partId) redictMicPart = null;
	}
}

// "Weiter diktieren" in the list row menu: answered once, as soon as the recording is loaded.
// A page that cannot take a part right then (dictating, post-processing, not transcribed)
// drops it, so the microphone never opens later on its own.
const redictationRequest = useRedictationRequest();
watch([redictationRequest, loading, recording], () => {
	if (redictationRequest.value !== id || loading.value || !recording.value) return;
	redictationRequest.value = null;
	if (canEdit.value && !postprocessing.value && pttState.value === "idle" && !redict.recording.value) {
		void startRedictation();
	}
}, { immediate: true, flush: "post" });

async function finishRedictation(): Promise<void> {
	const partId = redictMicPart;
	redictMicPart = null;
	if (!partId) return;
	editorRef.value?.setDictationPartStatus(partId, "transcribing");
	if (!await redict.finish()) editorRef.value?.removeDictationPart(partId);
}

async function cancelRedictation(): Promise<void> {
	const partId = redictMicPart;
	redictMicPart = null;
	if (partId) editorRef.value?.removeDictationPart(partId);
	await redict.cancel();
}

function retryRedictation(partId: string): void {
	editorRef.value?.setDictationPartStatus(partId, "transcribing");
	redict.retry(partId);
}

function retryRedictationAudio(partId: string): void {
	redictNotice.value = null;
	redict.retryAudio(partId);
}

function discardRedictation(partId: string): void {
	redict.discard(partId);
	editorRef.value?.removeDictationPart(partId);
}

/** Where the chip of `partId` stands in the saved text (null offset: at the end). */
async function anchorOf(partId: string): Promise<RedictationAnchor> {
	const saved = edit.committed.value;
	const chip = editorRef.value?.dictationPartOffsets().find(o => o.partId === partId);
	return {
		anchor_offset: chip ? rebaseOffset(saved, chip.base, chip.at) : null,
		anchor_text_sha: await sha256Hex(saved),
	};
}

/**
 * A stored part this page did not record (the recording was opened again): its
 * chip goes back at its anchor while the saved text is the one it refers to, else
 * at the end with a notice. Waits one tick so a just-seeded editor is mounted.
 */
async function restoreRedictation(part: RecordingPart): Promise<void> {
	await nextTick();
	const ed = showRendered.value ? null : editorRef.value;
	if (!ed) return;
	const status = part.status === "error" ? "error" : "transcribing";
	const sameText = part.anchor_text_sha != null && part.anchor_text_sha === await sha256Hex(edit.committed.value);
	const atAnchor = sameText && part.anchor_offset != null;
	if (!atAnchor && part.anchor_offset != null) redictNotice.value = MOVED_TO_END;
	ed.insertDictationPart(part.id, atAnchor ? part.anchor_offset! : "end", status);
}

// One `\n` is one paragraph, so a single newline starts a new one (none when the
// text is empty or already ends in one).
function joinParagraph(current: string, text: string): string {
	const separator = current.length === 0 || current.endsWith("\n") ? "" : "\n";
	return current + separator + text;
}

// Through the editor so the caret, selection and undo history stay where they
// are; without a mounted editor, on the text directly.
function appendParagraph(text: string): void {
	if (editorRef.value) {
		editorRef.value.appendParagraph(text);
		return;
	}
	edit.text.value = joinParagraph(edit.text.value, text);
}

/**
 * A part's text replaces its chip as a paragraph of its own and is saved on its
 * own: placed into the saved text at the chip's place, so unsaved typing is never
 * written along (a part inside unsaved typing stays unsaved). Without the chip
 * (no editor meanwhile) it is appended at the end.
 */
async function landRedictation(partId: string, text: string): Promise<boolean> {
	const placed = editorRef.value?.resolveDictationPart(partId, text, { paragraph: true }) ?? null;
	if (!text) {
		redictNotice.value = "Im neuen Teil wurde kein Text erkannt.";
		return true;
	}
	partTexts.value = [...partTexts.value, text];
	let saved: boolean;
	if (placed) {
		const { base, at, inserted } = placed;
		saved = await autosavePart(text, current => rebaseInsertion(current, base, at, inserted));
	}
	else {
		appendParagraph(text);
		saved = await autosavePart(text, current => joinParagraph(current, text));
	}
	if (saved) recordingsList.request();
	else redictNotice.value = "Text eingefügt, aber nicht gespeichert – bitte Speichern.";
	return saved;
}

function applyRedictationAudio(result: RedictationAudio): void {
	if (!result.ok) {
		redictNotice.value = `Audio konnte nicht angehängt werden: ${result.error}`;
		return;
	}
	if (recording.value) {
		// The audio watcher re-resolves the player source from the new file id.
		recording.value.audio_file = result.audio_file;
		recording.value.duration_seconds = result.duration_seconds;
	}
	recordingsList.request();
}

// A part still recording, being stored or appended, or one that could not be
// stored (it lives only in memory), is lost by leaving, so ask first. Stored parts
// keep transcribing without the page. Nuxt keys the page by id, so switching to
// another recording (desktop list) is a route update on this same record. The
// same for a dictation still recording or transcribing (chips add nothing to the
// text, so the save dialog would not ask).
const REDICTATION_LEAVE = "Das laufende Nachdiktat geht verloren. Trotzdem verlassen?";
const DICTATION_LEAVE = "Das laufende Diktat geht verloren. Trotzdem verlassen?";
const FAILED_LEAVE = "Ein Nachdiktat konnte nicht gespeichert werden und geht beim Verlassen verloren. Trotzdem verlassen?";

function confirmLeave(): boolean {
	if (redict.busy.value) return window.confirm(REDICTATION_LEAVE);
	if (redict.failed.value > 0) return window.confirm(FAILED_LEAVE);
	if (pttState.value !== "idle") return window.confirm(DICTATION_LEAVE);
	return true;
}

// Unsaved text edits: ask whether to save or discard them before leaving.
// A dialog (not window.confirm) because it has three outcomes.
const leaveDialog = ref(false);
let leaveResolve: ((leave: boolean) => void) | null = null;

function askUnsaved(): Promise<boolean> {
	leaveDialog.value = true;
	return new Promise((resolve) => {
		leaveResolve = resolve;
	});
}

function answerLeave(leave: boolean): void {
	leaveDialog.value = false;
	leaveResolve?.(leave);
	leaveResolve = null;
}

async function leaveSave(): Promise<void> {
	if (await saveTranscript()) {
		recordingsList.request();
		answerLeave(true);
	}
	else {
		// Saving failed: stay, the text is still in the editor and the error shows.
		answerLeave(false);
	}
}

function leaveDiscard(): void {
	discardEdits();
	answerLeave(true);
}

function guardLeave(): boolean | Promise<boolean> {
	if (!confirmLeave()) return false;
	// A part still being saved may fail and become unsaved: decide once the writes settled.
	if (edit.saving.value) {
		return edit.enqueue(async () => dirty.value).then(unsaved => (unsaved ? askUnsaved() : true));
	}
	if (!dirty.value) return true;
	return askUnsaved();
}

onBeforeRouteLeave(() => guardLeave());
onBeforeRouteUpdate((to, from) =>
	(to.params as { id?: string }).id === (from.params as { id?: string }).id || guardLeave());

// Closing the tab or reloading with unsaved edits, a write still running or a
// dictation in flight (useRedictation guards its own parts, failed ones included).
function onBeforeUnload(e: BeforeUnloadEvent): void {
	if (dirty.value || edit.saving.value || pttState.value !== "idle") {
		e.preventDefault();
	}
}

const restarting = ref(false);

async function restartTranscription(): Promise<void> {
	restarting.value = true;
	try {
		// Allow the editor to re-seed from the freshly produced transcript.
		seeded.value = false;
		await store.updateRecording(id, { status: "pending", error_message: null });
		recordingsList.request();
		startPolling();
	}
	finally {
		restarting.value = false;
	}
}

const confirmingDelete = ref(false);
const deleting = ref(false);

// One back rule shared with the other pages (see useBackNav).
const { back, leaveTo } = useBackNav();

function goBack(): void {
	void back("/");
}

const canShare = typeof navigator !== "undefined" && typeof navigator.share === "function";

async function shareTranscript(): Promise<void> {
	if (!recording.value) return;
	try {
		await navigator.share({
			title: recording.value.title || "Aufnahme",
			text: toText(recording.value, currentTranscript()),
		});
	}
	catch {
		// cancelled by the user or unsupported content: nothing to do
	}
}

function copyTranscript(): void {
	if (!recording.value) {
		return;
	}
	void copy(toText(recording.value, currentTranscript()));
}

function exportTxt(): void {
	if (!recording.value) {
		return;
	}
	downloadText(recording.value, currentTranscript());
}

function exportMd(): void {
	if (!recording.value) {
		return;
	}
	downloadMarkdown(recording.value, currentTranscript());
}

// ⋯ in the header: Nachbearbeiten (a dialog), downloads and Löschen.
type MoreAction = "postprocess" | "txt" | "md" | "delete";
const postprocessOpen = ref(false);
const moreItems = computed<HMenuItem<MoreAction>[]>(() => [
	...(canEdit.value && transcript.value ? [{ id: "postprocess" as const, label: "Nachbearbeiten…" }] : []),
	{ id: "txt", label: "Als Text herunterladen" },
	{ id: "md", label: "Als Markdown herunterladen" },
	{ id: "delete", label: "Löschen", danger: true },
]);

function onMoreAction(action: MoreAction): void {
	if (action === "postprocess") postprocessOpen.value = true;
	else if (action === "txt") exportTxt();
	else if (action === "md") exportMd();
	else askDelete();
}

function askDelete(): void {
	confirmingDelete.value = true;
}

function cancelDelete(): void {
	confirmingDelete.value = false;
}

async function confirmDelete(): Promise<void> {
	deleting.value = true;
	try {
		await store.deleteRecording(id);
		recordingsList.request();
		void leaveTo("/");
	}
	finally {
		deleting.value = false;
		confirmingDelete.value = false;
	}
}

onMounted(() => {
	startPolling();
	window.addEventListener("keydown", onKeyDown);
	window.addEventListener("keyup", onKeyUp);
	window.addEventListener("beforeunload", onBeforeUnload);
});

onUnmounted(() => {
	stopPolling();
	audioDisposed = true;
	revokeAudio(audioSrc.value);
	audioSrc.value = null;
	window.removeEventListener("keydown", onKeyDown);
	window.removeEventListener("keyup", onKeyUp);
	window.removeEventListener("beforeunload", onBeforeUnload);
	if (redictationRequest.value === id) redictationRequest.value = null;
});
</script>

<template>
	<div
		class="detail"
		:class="{ 'detail--pane': paneInLayout }"
	>
		<header class="detail__bar">
			<button
				v-if="!paneInLayout"
				type="button"
				class="detail__icon"
				aria-label="Zurück"
				title="Zurück"
				data-testid="back"
				@click="goBack"
			>
				<svg
					width="24"
					height="24"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					stroke-width="2"
					stroke-linecap="round"
					stroke-linejoin="round"
					aria-hidden="true"
				>
					<path d="M15 18l-6-6 6-6" />
				</svg>
			</button>
			<span class="detail__bar-spacer" />
			<div class="detail__actions">
				<template v-if="canEdit && (editing || dirty)">
					<button
						type="button"
						class="detail__icon"
						aria-label="Änderungen verwerfen"
						title="Änderungen verwerfen"
						:disabled="edit.saving.value"
						data-testid="discard-edits"
						@click="discardAndClose"
					>
						<svg
							width="22"
							height="22"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="2"
							stroke-linecap="round"
							stroke-linejoin="round"
							aria-hidden="true"
						>
							<path d="M18 6L6 18M6 6l12 12" />
						</svg>
					</button>
					<button
						type="button"
						class="detail__icon"
						:class="{ 'detail__icon--accent': dirty && !edit.saving.value }"
						:aria-label="edit.saving.value ? 'Speichert…' : 'Speichern'"
						:title="edit.saving.value ? 'Speichert…' : 'Speichern'"
						:aria-busy="edit.saving.value"
						:disabled="edit.saving.value || pttState !== 'idle' || postprocessing"
						data-testid="save-transcript"
						@click="saveAndClose"
					>
						<svg
							width="22"
							height="22"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="2"
							stroke-linecap="round"
							stroke-linejoin="round"
							aria-hidden="true"
						>
							<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
							<polyline points="17 21 17 13 7 13 7 21" />
							<polyline points="7 3 7 8 15 8" />
						</svg>
					</button>
				</template>
				<button
					v-else-if="canEdit && !postprocessing"
					type="button"
					class="detail__icon"
					aria-label="Bearbeiten"
					title="Bearbeiten"
					data-testid="edit"
					@click="startEditing"
				>
					<svg
						width="22"
						height="22"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						stroke-linejoin="round"
						aria-hidden="true"
					>
						<path d="M12 20h9" />
						<path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z" />
					</svg>
				</button>
				<button
					v-if="canEdit"
					type="button"
					class="detail__icon"
					:class="{ 'detail__icon--ready': released }"
					:aria-pressed="released"
					:aria-label="released ? 'Freigegeben – zurück in Bearbeitung' : 'Freigeben'"
					:title="released ? 'Freigegeben – zurück in Bearbeitung' : 'Freigeben'"
					:disabled="settingStatus"
					data-testid="review"
					@click="setReviewStatus(released ? 'editing' : 'ready')"
				>
					<svg
						width="22"
						height="22"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						stroke-linejoin="round"
						aria-hidden="true"
					>
						<circle
							cx="12"
							cy="12"
							r="9"
						/>
						<path d="M8 12.5l2.7 2.7L16 9.8" />
					</svg>
				</button>
				<button
					v-if="canShare"
					type="button"
					class="detail__icon"
					aria-label="Teilen"
					title="Teilen"
					data-testid="share"
					@click="shareTranscript"
				>
					<svg
						width="22"
						height="22"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						stroke-linejoin="round"
						aria-hidden="true"
					>
						<circle
							cx="18"
							cy="5"
							r="3"
						/>
						<circle
							cx="6"
							cy="12"
							r="3"
						/>
						<circle
							cx="18"
							cy="19"
							r="3"
						/>
						<path d="M8.6 13.5l6.8 4M15.4 6.5l-6.8 4" />
					</svg>
				</button>
				<button
					type="button"
					class="detail__icon"
					aria-label="Kopieren"
					title="Kopieren"
					data-testid="copy"
					@click="copyTranscript"
				>
					<svg
						width="22"
						height="22"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						stroke-linejoin="round"
						aria-hidden="true"
					>
						<rect
							x="9"
							y="9"
							width="13"
							height="13"
							rx="2"
						/>
						<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
					</svg>
				</button>
				<HMenu
					class="detail__more"
					label="Weitere Aktionen"
					:items="moreItems"
					@select="onMoreAction"
				/>
			</div>
		</header>

		<div
			v-if="confirmingDelete"
			class="detail__confirm"
			role="alert"
			data-testid="confirm-bar"
		>
			<span class="detail__confirm-text">Diese Aufnahme löschen?</span>
			<div class="detail__confirm-actions">
				<HButton
					variant="secondary"
					data-testid="cancel-delete"
					@click="cancelDelete"
				>
					Abbrechen
				</HButton>
				<HButton
					variant="primary"
					:disabled="deleting"
					data-testid="confirm-delete"
					@click="confirmDelete"
				>
					Löschen
				</HButton>
			</div>
		</div>

		<div class="detail__body">
			<p
				v-if="loading && !recording"
				class="detail__hint"
			>
				Lädt…
			</p>

			<p
				v-else-if="error"
				class="detail__error"
			>
				{{ error }}
			</p>

			<template v-else-if="recording">
				<section class="detail__meta">
					<input
						v-if="editingTitle"
						ref="titleInput"
						v-model="titleDraft"
						class="detail__title detail__title-input"
						type="text"
						aria-label="Titel"
						data-testid="title-input"
						@keydown.enter.prevent="commitTitle"
						@keydown.esc.prevent="cancelTitleEdit"
						@blur="commitTitle"
					>
					<h1
						v-else
						class="detail__title"
					>
						<button
							type="button"
							class="detail__title-btn"
							title="Titel ändern"
							data-testid="bar-title"
							@click="startTitleEdit"
						>
							{{ recording.title || "Aufnahme" }}
						</button>
					</h1>
					<p
						v-if="titleError"
						class="detail__tool-error"
						role="alert"
						data-testid="title-error"
					>
						{{ titleError }}
					</p>
					<p
						v-if="metaLine"
						class="detail__metaline tabular"
						data-testid="meta-line"
					>
						{{ metaLine }}
					</p>
					<ul
						v-if="hubSettings.expert && provenanceLines.length"
						class="detail__provenance tabular"
						data-testid="provenance"
					>
						<li
							v-for="line in provenanceLines"
							:key="line"
						>
							{{ line }}
						</li>
					</ul>
					<div
						v-if="recording.status !== 'done'"
						class="detail__state"
					>
						<StatusChip :status="recording.status" />
					</div>
				</section>

				<div
					v-if="recording.status === 'error'"
					class="detail__banner"
					:class="{ 'detail__banner--offline': offlineError }"
					role="alert"
					data-testid="error-banner"
				>
					<svg
						class="detail__banner-icon"
						width="20"
						height="20"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						stroke-linejoin="round"
						aria-hidden="true"
					>
						<template v-if="offlineError">
							<path d="M22.61 16.95A5 5 0 0 0 18 10h-1.26a8 8 0 0 0-7.05-6M5 5a8 8 0 0 0 4 15h9a5 5 0 0 0 1.7-.3" />
							<line
								x1="1"
								y1="1"
								x2="23"
								y2="23"
							/>
						</template>
						<template v-else>
							<circle
								cx="12"
								cy="12"
								r="10"
							/>
							<line
								x1="12"
								y1="8"
								x2="12"
								y2="12"
							/>
							<line
								x1="12"
								y1="16"
								x2="12.01"
								y2="16"
							/>
						</template>
					</svg>
					<div class="detail__banner-text">
						{{ recording.error_message ?? "Bei der Transkription ist ein Fehler aufgetreten." }}
						<span
							v-if="offlineError"
							class="detail__banner-hint"
						>
							Der Transkriptionsdienst war nicht erreichbar – bitte später erneut versuchen.
						</span>
					</div>
					<HButton
						variant="secondary"
						:disabled="restarting || redictUnsettled"
						data-testid="restart-transcription"
						@click="restartTranscription"
					>
						{{ restarting ? "…" : "Neu transkribieren" }}
					</HButton>
				</div>

				<p
					v-else-if="recording.status === 'pending' || recording.status === 'transcribing'"
					class="detail__hint"
				>
					Transkribiert…
				</p>

				<template v-if="canEdit">
					<div class="detail__statusrow">
						<span
							class="detail__save-state"
							:class="{ 'detail__save-state--dirty': dirty }"
							aria-live="polite"
							data-testid="save-state"
						>{{ edit.saving.value ? "Speichert…" : dirty ? "Ungespeicherte Änderungen" : "Gespeichert" }}</span>
						<span
							v-if="redict.startError.value"
							class="detail__tool-error"
							role="alert"
							data-testid="continue-error"
						>{{ redict.startError.value }}</span>
					</div>
					<p
						v-if="pttError"
						class="detail__error"
						data-testid="dictate-error"
					>
						{{ pttError }}
					</p>
					<p
						v-if="edit.error.value"
						class="detail__error"
						data-testid="save-error"
					>
						{{ edit.error.value }}
					</p>

					<!-- One live region, always in the DOM, so screen readers announce the
						progress text and the notice when they appear. -->
					<div
						:class="redictProgress
							? 'detail__banner detail__banner--progress'
							: redictNotice ? 'detail__banner detail__banner--notice' : 'sr-only'"
					>
						<span
							v-if="redictProgress"
							class="detail__spinner"
							aria-hidden="true"
						/>
						<div
							class="detail__banner-text"
							role="status"
							aria-live="polite"
							data-testid="continue-live"
						>
							<span
								v-if="redictProgress"
								data-testid="continue-banner"
							>{{ redictProgress }}</span>
							<span
								v-else-if="redictNotice"
								data-testid="continue-notice"
							>{{ redictNotice }}</span>
						</div>
					</div>

					<div
						v-for="part in redictFailed"
						:key="part.id"
						class="detail__banner"
						role="alert"
						data-testid="continue-failed"
					>
						<div class="detail__banner-text">
							{{ part.error }}
							<span class="detail__banner-hint">Text erneut anfordern oder verwerfen.</span>
						</div>
						<HButton
							variant="secondary"
							data-testid="continue-discard"
							@click="discardRedictation(part.id)"
						>
							Verwerfen
						</HButton>
						<HButton
							variant="primary"
							data-testid="continue-retry"
							@click="retryRedictation(part.id)"
						>
							Erneut versuchen
						</HButton>
					</div>

					<div
						v-for="part in redictAudioFailed"
						:key="`audio-${part.id}`"
						class="detail__banner"
						role="alert"
						data-testid="continue-audio-failed"
					>
						<div class="detail__banner-text">
							Audio nicht gespeichert – Erneut anhängen
							<span class="detail__banner-hint">{{ part.audioError }} Das Audio dieses Nachdiktats ist nur auf dieser Seite und geht beim Verlassen verloren.</span>
						</div>
						<HButton
							variant="secondary"
							data-testid="continue-audio-discard"
							@click="discardRedictation(part.id)"
						>
							Verwerfen
						</HButton>
						<HButton
							variant="primary"
							data-testid="continue-audio-retry"
							@click="retryRedictationAudio(part.id)"
						>
							Erneut anhängen
						</HButton>
					</div>

					<PostprocessPanel
						v-if="transcript"
						v-model:open="postprocessOpen"
						:transcript="transcript"
						:blocked="postprocessBlocked"
						@changed="onPostprocessChanged"
					/>
					<div class="detail__surface">
						<MarkdownView
							v-if="showRendered"
							:text="edit.text.value"
							data-testid="transcript-rendered"
						/>
						<TranscriptEditor
							v-else
							ref="editorRef"
							v-model="edit.text.value"
							:editable="editing && !postprocessing"
							:movable="wide"
							:sections="partTexts"
							data-testid="transcript-edit"
							placeholder="Noch kein Transkript – hier tippen oder diktieren."
							@typed="typedEdits += 1"
						/>
					</div>
				</template>
			</template>
		</div>

		<div
			v-if="leaveDialog"
			class="detail__confirm detail__leave"
			role="alertdialog"
			aria-label="Ungespeicherte Änderungen"
			data-testid="leave-dialog"
		>
			<span class="detail__confirm-text">Änderungen am Text speichern?</span>
			<div class="detail__confirm-actions">
				<HButton
					variant="secondary"
					data-testid="leave-stay"
					@click="answerLeave(false)"
				>
					Bleiben
				</HButton>
				<HButton
					variant="secondary"
					:disabled="edit.saving.value"
					data-testid="leave-discard"
					@click="leaveDiscard"
				>
					Verwerfen
				</HButton>
				<HButton
					variant="primary"
					:disabled="edit.saving.value"
					data-testid="leave-save"
					@click="leaveSave"
				>
					{{ edit.saving.value ? "Speichert…" : "Speichern" }}
				</HButton>
			</div>
		</div>

		<div
			v-if="canRecord || (recording?.audio_file && audioSrc)"
			class="detail__foot"
		>
			<HRecordBar
				v-if="redict.recording.value"
				label="Nachdiktat"
				:elapsed="redict.recorder.elapsed.value"
				:levels="redict.recorder.levels.value"
				:paused="redict.recorder.paused.value"
				:pending="redict.transcribing.value"
				:interruption="redict.recorder.interruption.value"
				@pause="redict.recorder.pause()"
				@resume="redict.recorder.resume()"
				@continue="redict.recorder.continueRecording()"
				@stop="finishRedictation"
				@cancel="cancelRedictation"
			/>
			<HPlayerDock
				v-else-if="recording?.audio_file && audioSrc"
				:src="audioSrc"
				:fallback-duration="recording.duration_seconds ?? undefined"
				:marks="partMarks"
			>
				<HRecordButton
					v-if="canRecord"
					:label="recordLabel"
					:active="pttState === 'recording'"
					:busy="pttState === 'transcribing'"
					:disabled="pttState === 'transcribing'"
					data-testid="record"
					@press="noteRecordPress"
					@tap="startRedictation"
					@hold-start="startDictation"
					@hold-end="releaseDictation"
				/>
			</HPlayerDock>
			<div
				v-else
				class="detail__recrow"
			>
				<HRecordButton
					:label="recordLabel"
					:active="pttState === 'recording'"
					:busy="pttState === 'transcribing'"
					:disabled="pttState === 'transcribing'"
					data-testid="record"
					@press="noteRecordPress"
					@tap="startRedictation"
					@hold-start="startDictation"
					@hold-end="releaseDictation"
				/>
			</div>
		</div>
	</div>
</template>

<style scoped>
/* Phone: the page owns the viewport (no shell header on this route). Desktop pane:
   the page fills the scrolling right pane (a flex column); the dock is sticky to it. */
.detail { min-height:100dvh; display:flex; flex-direction:column; background:var(--bg); }
.detail--pane { min-height:0; flex:1 0 auto; }
.detail__bar { position:sticky; top:0; z-index:5; display:flex; align-items:center; gap:8px; padding:10px 12px 10px 16px; padding-top:calc(10px + env(safe-area-inset-top)); background:var(--bg); border-bottom:1px solid var(--border); }
.detail__bar-spacer { flex:1; }
.detail__title-btn { all:unset; cursor:text; border-radius:6px; padding:2px 6px; margin:-2px -6px; overflow-wrap:anywhere; }
.detail__title-btn:hover { background:var(--crust-soft); }
.detail__title-btn:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.detail__title-input { width:100%; box-sizing:border-box; padding:2px 8px; margin:-4px -8px; border:1.5px solid var(--accent); border-radius:8px; background:var(--white); }
.detail__leave { position:sticky; bottom:0; z-index:6; }
.detail__actions { display:flex; align-items:center; gap:2px; }
.detail__icon { width:42px; height:42px; flex:0 0 auto; border:none; border-radius:50%; background:transparent; color:var(--crust-deep); display:inline-flex; align-items:center; justify-content:center; cursor:pointer; transition:background var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease); }
.detail__icon:disabled { opacity:0.4; cursor:default; background:transparent; }
.detail__icon--ready { color:var(--olive-deep); }
.detail__more :deep(.h-menu__trigger) { width:42px; height:42px; color:var(--crust-deep); }
.detail__more :deep(.h-menu__trigger svg) { width:22px; height:22px; }
.detail__icon--accent { color:var(--accent); }
.detail__icon:hover:not(:disabled) { background:var(--cream-deep); }
.detail__icon:active:not(:disabled) { background:var(--crust-soft); }
.detail__confirm { display:flex; align-items:center; gap:12px; padding:12px 20px; background:var(--tomato-soft); }
.detail__confirm-text { flex:1; font-family:var(--font-body); font-size:14px; font-weight:600; color:var(--tomato-deep); }
.detail__confirm-actions { display:flex; gap:8px; }
.detail__body { flex:1; width:100%; max-width:72ch; margin:0 auto; box-sizing:border-box; display:flex; flex-direction:column; gap:16px; padding:20px 20px 24px; }
.detail__meta { display:flex; flex-direction:column; gap:6px; }
.detail__title { margin:0; font-family:var(--font-display); font-size:var(--fs-xl,22px); font-weight:600; line-height:1.25; color:var(--fg1); overflow-wrap:anywhere; }
.detail__metaline { margin:0; font-family:var(--font-mono); font-size:12px; color:var(--fg3); font-variant-numeric:tabular-nums; }
.detail__provenance { margin:0; padding:0; list-style:none; font-family:var(--font-mono); font-size:12px; color:var(--fg3); }
.detail__state { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-top:6px; }
.detail__hint { color:var(--fg3); font-size:15px; margin:0; }
.detail__error { color:var(--danger); font-size:14px; margin:0; }
.detail__banner { display:flex; align-items:center; flex-wrap:wrap; gap:12px; background:var(--tomato-soft); color:var(--tomato-deep); border-radius:12px; padding:12px 14px; font-family:var(--font-body); font-size:14px; }
.detail__banner--offline { background:var(--butter-soft); color:#8B6200; }
.detail__banner--notice { margin:0; background:var(--butter-soft); color:#8B6200; }
.detail__banner--progress { background:var(--bg-sunken); color:var(--fg1); }
.detail__spinner { flex-shrink:0; width:16px; height:16px; border-radius:50%; border:2px solid var(--border-strong); border-top-color:var(--accent); animation:detail-spin 0.9s linear infinite; }
@keyframes detail-spin { to { transform:rotate(360deg); } }
@media (prefers-reduced-motion:reduce) { .detail__spinner { animation-duration:2.4s; } }
.detail__banner-icon { flex-shrink:0; align-self:flex-start; margin-top:1px; }
.detail__banner-text { flex:1; min-width:180px; display:flex; flex-direction:column; gap:4px; }
.detail__banner-hint { font-size:13px; opacity:0.85; }
.detail__tool-error { flex-basis:100%; color:var(--danger); font-family:var(--font-body); font-size:13px; }
.detail__save-state { font-family:var(--font-body); font-size:13px; color:var(--fg3); }
.detail__save-state--dirty { color:var(--accent-ink,var(--accent)); font-weight:600; }
.detail__surface { flex:1; display:flex; flex-direction:column; min-height:240px; }
.detail__surface > :deep(*) { flex:1; }
.detail__foot { position:sticky; bottom:0; z-index:5; background:var(--white); padding-bottom:env(safe-area-inset-bottom); }
.detail__foot .h-dock { border-top:1px solid var(--border); }
.detail__recrow { display:flex; justify-content:flex-end; padding:12px 20px 16px; border-top:1px solid var(--border); }
.detail__statusrow { display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap; }
</style>
