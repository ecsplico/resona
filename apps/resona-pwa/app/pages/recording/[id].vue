<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { humanizeError, isServiceUnreachable } from "#shared/errors";
import { HubError } from "~/composables/useHub";
import type { RedictationAudio } from "~/composables/useRedictation";
import type { Recording, Transcript } from "~/types/directus";
import { appendToRaw, postprocessRunning } from "~/utils/postprocess";
import { rebaseInsertion } from "~/utils/correction";

const route = useRoute();
const id = String(route.params.id);
const cfg = useRuntimeConfig().public;

const { recording, transcript, loading, error, refresh, startPolling, stopPolling } = useRecording(id);
const store = useDataStore();
const { paneInLayout } = useShell();
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
	return true;
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

// A profile's output is shown rendered (headings, paragraphs); "Bearbeiten"
// switches to the editor, which also opens for dictation.
const editingProcessed = ref(false);
const showRendered = computed(() => Boolean(transcript.value?.applied_profile) && !editingProcessed.value);

function reseed(): void {
	edit.seed(transcript.value?.text ?? "");
	forgetUnsaved();
	editingProcessed.value = false;
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
		await store.updateRecording(id, { title: next, title_auto: false });
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
const settingStatus = ref(false);

async function setReviewStatus(value: "editing" | "ready"): Promise<void> {
	if (!recording.value || reviewStatus.value === value) {
		return;
	}
	settingStatus.value = true;
	try {
		await store.updateRecording(id, { review_status: value });
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
	insertDictationPart: (partId: string, at: "caret" | "end") => void;
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
	// Dictation inserts at the caret, so the rendered view gives way to the editor.
	if (showRendered.value) {
		editingProcessed.value = true;
		await nextTick();
	}
	pttPart = `ptt-${++pttSeq}`;
	// At the caret when the user is in the text, else at its end.
	const ed = editorRef.value;
	ed?.insertDictationPart(pttPart, ed.hasFocus() ? "caret" : "end");
	try {
		await pttRecorder.start();
		pttState.value = "recording";
		pttDiag.record("mic", true, "Mikrofonzugriff erteilt.");
	}
	catch (err) {
		dropPttPart();
		pttDiag.record("mic", false, describeError(err));
		pttError.value = "Mikrofon nicht verfügbar.";
	}
}

async function stopDictation(): Promise<void> {
	pttLatched.value = false;
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

// Touch has no Ctrl key and holding a button while the thumb also has to reach
// the text is awkward: a short tap latches dictation on, the next tap stops it;
// holding (>= 400 ms) keeps the old push-to-talk behavior (stop on release).
const PTT_HOLD_MS = 400;
let pttDownAt = 0;
const pttLatched = ref(false);

function onPttDown(e: PointerEvent): void {
	if (pttLatched.value) return;
	pttDownAt = Date.now();
	try {
		(e.currentTarget as Element | null)?.setPointerCapture?.(e.pointerId);
	}
	catch {
		// pointer capture unsupported in test env
	}
	void startDictation();
}

function onPttUp(e: PointerEvent): void {
	try {
		(e.currentTarget as Element | null)?.releasePointerCapture?.(e.pointerId);
	}
	catch {
		// pointer capture unsupported in test env
	}
	if (pttLatched.value) {
		pttLatched.value = false;
		void stopDictation();
		return;
	}
	if (e.type === "pointerup" && Date.now() - pttDownAt < PTT_HOLD_MS && pttState.value === "recording") {
		pttLatched.value = true;
		return;
	}
	void stopDictation();
}

function onKeyDown(e: KeyboardEvent): void {
	if (e.code === pttKeyCode && !e.repeat && pttState.value === "idle" && !redict.recording.value && !postprocessing.value) {
		e.preventDefault();
		void startDictation();
	}
}

function onKeyUp(e: KeyboardEvent): void {
	if (e.code === pttKeyCode && pttState.value === "recording") {
		e.preventDefault();
		void stopDictation();
	}
}

// ── Re-dictation (record more parts; each lands where its chip stands) ─────
// The bar records one part at a time; a stopped part's audio is appended to the
// recording and its hub job runs in the background, so the next part, typing and
// saving never wait. Each part is a chip in the editor until its text arrives.
const redict = useRedictation({
	recording: () => recording.value,
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
	if (n > 0) return `${n === 1 ? "1 Teil wird" : `${n} Teile werden`} transkribiert – Seite bitte geöffnet lassen.`;
	return redict.merging.value ? "Audio wird angehängt…" : null;
});
// Why "Nachbearbeiten" cannot start now: it runs on the stored text, so unsaved
// edits, writes still running and dictation/re-dictation in progress must settle first.
const postprocessBlocked = computed(() => {
	if (dirty.value) return "Erst speichern oder verwerfen, dann nachbearbeiten.";
	if (edit.saving.value) return "Der Text wird noch gespeichert.";
	if (pttState.value !== "idle" || redict.busy.value || redict.failed.value > 0) return "Erst die laufende Aufnahme abschließen.";
	return null;
});

let redictSeq = 0;
// The part the record bar is recording (null: bar closed).
let redictMicPart: string | null = null;
// Pressing the button moves the focus out of the text, so whether the user was
// editing is noted on pointerdown, before that happens.
let editingAtPress = false;

function noteRedictPress(): void {
	editingAtPress = editorRef.value?.hasFocus() ?? false;
}

async function startRedictation(): Promise<void> {
	if (redict.recording.value || pttState.value !== "idle") return;
	redictNotice.value = null;
	const partId = `part-${++redictSeq}`;
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

// "Weiter diktieren" in the list row menu: start a part once this recording takes one.
const redictationRequest = useRedictationRequest();
const redictationReady = computed(() =>
	redictationRequest.value === id && canEdit.value && !postprocessing.value && pttState.value === "idle" && !redict.recording.value,
);
watch(redictationReady, (ready) => {
	if (!ready) return;
	redictationRequest.value = null;
	void startRedictation();
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
async function landRedictation(partId: string, text: string): Promise<void> {
	const placed = editorRef.value?.resolveDictationPart(partId, text, { paragraph: true }) ?? null;
	if (!text) {
		redictNotice.value = "Im neuen Teil wurde kein Text erkannt.";
		return;
	}
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

// A part still recording, transcribing or being appended, or one that failed (its
// audio blob lives only in memory), lives only in this page: leaving drops it, so ask first. Nuxt keys the page by id, so switching to
// another recording (desktop list) is a route update on this same record. The
// same for a dictation still recording or transcribing (chips add nothing to the
// text, so the save dialog would not ask).
const REDICTATION_LEAVE = "Das laufende Nachdiktat geht verloren. Trotzdem verlassen?";
const DICTATION_LEAVE = "Das laufende Diktat geht verloren. Trotzdem verlassen?";
const FAILED_LEAVE = "Ein Nachdiktat ist fehlgeschlagen und geht beim Verlassen verloren. Trotzdem verlassen?";

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
					<path d="M15 18l-6-6 6-6" />
				</svg>
			</button>
			<input
				v-if="editingTitle"
				ref="titleInput"
				v-model="titleDraft"
				class="detail__title-input"
				type="text"
				aria-label="Titel"
				data-testid="title-input"
				@keydown.enter.prevent="commitTitle"
				@keydown.esc.prevent="cancelTitleEdit"
				@blur="commitTitle"
			>
			<button
				v-else
				type="button"
				class="detail__bar-title detail__bar-title--btn"
				title="Titel ändern"
				data-testid="bar-title"
				:disabled="!recording"
				@click="startTitleEdit"
			>
				{{ recording?.title || "Aufnahme" }}
			</button>
			<span
				v-if="titleError"
				class="detail__tool-error"
				role="alert"
				data-testid="title-error"
			>{{ titleError }}</span>
			<div class="detail__actions">
				<template v-if="canEdit && dirty">
					<HButton
						variant="secondary"
						:disabled="edit.saving.value"
						data-testid="discard-edits"
						@click="discardEdits"
					>
						Verwerfen
					</HButton>
				</template>
				<HButton
					v-if="canEdit"
					variant="primary"
					:disabled="edit.saving.value || pttState !== 'idle' || postprocessing || !dirty"
					data-testid="save-transcript"
					@click="saveTranscript()"
				>
					{{ edit.saving.value ? "Speichert…" : "Speichern" }}
				</HButton>
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
						width="18"
						height="18"
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
						width="18"
						height="18"
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
				<ExportMenu
					@txt="exportTxt"
					@md="exportMd"
				/>
				<button
					type="button"
					class="detail__icon detail__icon--danger"
					aria-label="Löschen"
					title="Löschen"
					data-testid="delete"
					@click="askDelete"
				>
					<svg
						width="18"
						height="18"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						stroke-linejoin="round"
						aria-hidden="true"
					>
						<path d="M3 6h18" />
						<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
						<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
						<line
							x1="10"
							y1="11"
							x2="10"
							y2="17"
						/>
						<line
							x1="14"
							y1="11"
							x2="14"
							y2="17"
						/>
					</svg>
				</button>
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
					<h1 class="detail__title">
						{{ recording.title || "Aufnahme" }}
					</h1>
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
						v-if="recording.status !== 'done' || canEdit"
						class="detail__state"
					>
						<StatusChip
							v-if="recording.status !== 'done'"
							:status="recording.status"
						/>
						<ReviewSwitch
							v-if="canEdit"
							:model-value="reviewStatus ?? 'editing'"
							:disabled="settingStatus"
							@update:model-value="setReviewStatus"
						/>
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
						:disabled="restarting || redict.busy.value || redict.failed.value > 0"
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
						:transcript="transcript"
						:blocked="postprocessBlocked"
						@changed="onPostprocessChanged"
					/>
					<div
						v-if="transcript?.applied_profile && !postprocessing"
						class="detail__viewbar"
					>
						<button
							type="button"
							class="detail__viewtoggle"
							data-testid="transcript-view-toggle"
							@click="editingProcessed = !editingProcessed"
						>
							{{ showRendered ? "Bearbeiten" : "Formatiert anzeigen" }}
						</button>
					</div>
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
							:editable="!postprocessing"
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
			v-if="canEdit || (recording?.audio_file && audioSrc)"
			class="detail__foot"
		>
			<div
				v-if="canEdit && !postprocessing"
				class="detail__tools"
				role="group"
				aria-label="Diktieren und weiter aufnehmen"
			>
				<button
					type="button"
					class="detail__ptt"
					:class="{
						'detail__ptt--active': pttState === 'recording',
						'detail__ptt--busy': pttState === 'transcribing',
					}"
					:disabled="pttState === 'transcribing' || redict.recording.value"
					data-testid="push-to-talk"
					:aria-pressed="pttState === 'recording'"
					:aria-label="`Zum Diktieren gedrückt halten (${pttKeyLabel})`"
					@pointerdown.prevent="onPttDown"
					@pointerup="onPttUp"
					@pointercancel="onPttUp"
				>
					<svg
						width="18"
						height="18"
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
							y="2"
							width="6"
							height="12"
							rx="3"
						/>
						<path d="M5 10a7 7 0 0 0 14 0" />
						<line
							x1="12"
							y1="17"
							x2="12"
							y2="21"
						/>
					</svg>
					<span>{{
						pttState === "recording" ? (pttLatched ? "Aufnahme… tippen zum Beenden" : "Aufnahme… loslassen zum Transkribieren")
						: pttState === "transcribing" ? "Transkribiert…"
							: `Diktieren (tippen oder ${pttKeyLabel} halten)`
					}}</span>
				</button>
				<button
					type="button"
					class="detail__ptt"
					:disabled="pttState !== 'idle' || redict.recording.value"
					data-testid="continue-recording"
					@pointerdown="noteRedictPress"
					@click="startRedictation"
				>
					<svg
						width="18"
						height="18"
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
						<circle
							cx="12"
							cy="12"
							r="3.5"
							fill="currentColor"
						/>
					</svg>
					<span>Weiter aufnehmen</span>
				</button>
			</div>
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
			/>
		</div>
	</div>
</template>

<style scoped>
/* Phone: the page owns the viewport (no shell header on this route). Desktop pane:
   the page fills the scrolling right pane (a flex column); the dock is sticky to it. */
.detail { min-height:100dvh; display:flex; flex-direction:column; background:var(--bg); }
.detail--pane { min-height:0; flex:1 0 auto; }
.detail__bar { position:sticky; top:0; z-index:5; display:flex; align-items:center; gap:8px; padding:10px 12px 10px 16px; background:var(--bg); border-bottom:1px solid var(--border); }
.detail__bar-title--btn { background:none; border:0; padding:4px 6px; margin:-4px -6px; text-align:left; cursor:text; border-radius:6px; }
.detail__bar-title--btn:hover:not(:disabled) { background:var(--crust-soft); }
.detail__title-input { flex:1; min-width:0; font:inherit; font-family:var(--font-body); font-size:var(--fs-md); font-weight:600; padding:4px 8px; border:1.5px solid var(--accent); border-radius:8px; background:var(--white); color:var(--fg1); }
.detail__leave { position:sticky; bottom:0; z-index:6; }
.detail__bar-title { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-family:var(--font-body); font-size:var(--fs-md); font-weight:600; color:var(--fg1); }
.detail__actions { display:flex; align-items:center; gap:2px; }
.detail__icon { width:38px; height:38px; flex:0 0 auto; border:none; border-radius:50%; background:transparent; color:var(--crust-deep); display:inline-flex; align-items:center; justify-content:center; cursor:pointer; transition:background var(--motion-fast) var(--motion-ease); }
.detail__icon:hover { background:var(--cream-deep); }
.detail__icon:active { background:var(--crust-soft); }
.detail__icon--danger:hover { background:var(--tomato-soft); color:var(--tomato-deep); }
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
.detail__tools { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.detail__tool-error { flex-basis:100%; color:var(--danger); font-family:var(--font-body); font-size:13px; }
.detail__save-state { font-family:var(--font-body); font-size:13px; color:var(--fg3); }
.detail__save-state--dirty { color:var(--accent-ink,var(--accent)); font-weight:600; }
.detail__surface { flex:1; display:flex; flex-direction:column; min-height:240px; }
.detail__surface > :deep(*) { flex:1; }
.detail__viewbar { display:flex; justify-content:flex-end; margin:-8px 0 -8px; }
.detail__viewtoggle { border:none; background:transparent; padding:6px 4px; font-family:var(--font-body); font-size:var(--fs-sm); font-weight:600; color:var(--accent); cursor:pointer; text-decoration:underline; text-underline-offset:3px; }
.detail__ptt { display:inline-flex; align-items:center; gap:8px; padding:9px 16px; border-radius:999px; border:1.5px solid var(--border-strong); background:var(--white); color:var(--fg2); font-family:var(--font-body); font-weight:600; font-size:14px; cursor:pointer; touch-action:none; user-select:none; transition:background var(--motion-fast) var(--motion-ease), border-color var(--motion-fast) var(--motion-ease); }
.detail__ptt:hover:not(:disabled) { border-color:var(--accent); }
.detail__ptt--active { background:var(--accent); color:var(--fg-on-accent); border-color:var(--accent); }
.detail__ptt--busy { opacity:0.65; cursor:default; }
.detail__ptt:disabled:not(.detail__ptt--busy) { opacity:0.5; cursor:default; }
.detail__foot { position:sticky; bottom:0; z-index:5; background:var(--white); }
.detail__foot .detail__tools { padding:10px 16px 0; justify-content:center; border-top:1px solid var(--border); }
.detail__foot .h-dock { border-top:0; padding-top:10px; }
.detail__statusrow { display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap; }
</style>
