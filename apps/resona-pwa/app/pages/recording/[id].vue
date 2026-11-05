<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { isServiceUnreachable } from "#shared/errors";
import { HubError } from "~/composables/useHub";
import type { ContinueResult } from "~/composables/useContinueRecording";
import type { Recording, Transcript } from "~/types/directus";

const route = useRoute();
const id = String(route.params.id);
const cfg = useRuntimeConfig().public;

const { recording, transcript, loading, error, startPolling, stopPolling } = useRecording(id);
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
	}
	else if (canEdit.value) {
		edit.seed("");
		seeded.value = true;
	}
}, { immediate: true });

const dirty = computed(() => seeded.value && edit.text.value !== edit.committed.value);

/** Persists the editor text; false when the save failed (the text stays in the editor). */
async function saveTranscript(): Promise<boolean> {
	const target = edit.text.value;
	if (transcript.value) {
		await edit.saveEdit(transcript.value.id, target);
		return !edit.error.value;
	}
	// No transcript row yet -> create one for this recording.
	const created = await edit.createTranscript(id, target);
	if (!created) return false;
	transcript.value = created;
	seeded.value = true;
	return true;
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

// ── Push-to-talk dictation (batch REST, insert-at-marker) ──────────────────
type PttEditorRef = {
	insertText: (text: string) => void;
	insertPlaceholder: () => { from: number; to: number } | null;
	replaceBetween: (from: number, to: number, text: string) => void;
	appendParagraph: (text: string) => void;
	focus: () => void;
} | null;

const editorRef = ref<PttEditorRef>(null);
const pttState = ref<"idle" | "recording" | "transcribing">("idle");
const pttError = ref<string | null>(null);
const pttRecorder = useRecorder();
const pttDiag = usePttDiagnostics();
let pttRange: { from: number; to: number } | null = null;

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
	if (pttState.value !== "idle" || contActive.value) return;
	pttError.value = null;
	pttRange = editorRef.value?.insertPlaceholder() ?? null;
	try {
		await pttRecorder.start();
		pttState.value = "recording";
		pttDiag.record("mic", true, "Mikrofonzugriff erteilt.");
	}
	catch (err) {
		if (pttRange) {
			editorRef.value?.replaceBetween(pttRange.from, pttRange.to, "");
			pttRange = null;
		}
		pttDiag.record("mic", false, describeError(err));
		pttError.value = "Mikrofon nicht verfügbar.";
	}
}

async function stopDictation(): Promise<void> {
	if (pttState.value !== "recording") return;
	pttState.value = "transcribing";
	const blob = await pttRecorder.stop();
	if (!blob || blob.size === 0) {
		if (pttRange) {
			editorRef.value?.replaceBetween(pttRange.from, pttRange.to, "");
			pttRange = null;
		}
		pttDiag.record("record", false, "Leere Aufnahme (0 Bytes) — MediaRecorder hat keine Daten geliefert.");
		pttState.value = "idle";
		return;
	}
	const hub = useHub();
	if (!hub.hasToken()) {
		pttDiag.record("upload", false, "Kein Hub-Token – in Einstellungen → Verbindung eintragen");
		pttError.value = "Transkription fehlgeschlagen.";
		if (pttRange) {
			editorRef.value?.replaceBetween(pttRange.from, pttRange.to, "");
			pttRange = null;
		}
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
		if (pttRange) {
			// Trailing space so the next dictation/typing doesn't butt against it.
			editorRef.value?.replaceBetween(pttRange.from, pttRange.to, text ? `${text} ` : "");
			pttRange = null;
		}
	}
	catch (err) {
		if (!recordedFailure) {
			pttDiag.record("upload", false, describeError(err));
		}
		pttError.value = "Transkription fehlgeschlagen.";
		if (pttRange) {
			editorRef.value?.replaceBetween(pttRange.from, pttRange.to, "");
			pttRange = null;
		}
	}
	finally {
		pttState.value = "idle";
	}
}

function onPttDown(e: PointerEvent): void {
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
	void stopDictation();
}

function onKeyDown(e: KeyboardEvent): void {
	if (e.code === pttKeyCode && !e.repeat && pttState.value === "idle" && !contActive.value) {
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

// ── Continue recording (record more, append the new part's text) ──────────
const cont = useContinueRecording();
// Mic open or the new part's job/merge running: dictation and saving wait.
const contActive = computed(() => ["recording", "transcribing", "merging"].includes(cont.phase.value));
const contWorking = computed(() => cont.phase.value === "transcribing" || cont.phase.value === "merging");
// A recorded part whose transcription failed: banner with retry/discard.
const contRetained = computed(() => cont.phase.value === "error" && cont.hasRetainedPart.value);
// Failure before a part exists (mic refused, empty recording): shown in the toolbar.
const contStartError = computed(() =>
	cont.phase.value === "error" && !cont.hasRetainedPart.value ? cont.error.value : null,
);
const contNotice = ref<string | null>(null);

async function startContinue(): Promise<void> {
	contNotice.value = null;
	await cont.start();
}

// Through the editor so the caret, selection and undo history stay where they
// are. Without a mounted editor: one `\n` is one paragraph, so a single
// newline starts a new one (none when the text is empty or already ends in one).
function appendParagraph(text: string): void {
	if (editorRef.value) {
		editorRef.value.appendParagraph(text);
		return;
	}
	const current = edit.text.value;
	const separator = current.length === 0 || current.endsWith("\n") ? "" : "\n";
	edit.text.value = current + separator + text;
}

async function applyContinueResult(result: ContinueResult | null): Promise<void> {
	if (!result) return;
	if (result.audioMerged && result.audio_file && recording.value) {
		// The audio watcher re-resolves the player source from the new file id.
		recording.value.audio_file = result.audio_file;
		recording.value.duration_seconds = result.duration_seconds;
	}
	// Saves the user's unsaved edits together with the new part.
	let saved = true;
	if (result.text) {
		appendParagraph(result.text);
		saved = await saveTranscript();
	}
	if (result.text || result.audioMerged) recordingsList.request();
	const textNotice = !result.text
		? "Im neuen Teil wurde kein Text erkannt."
		: saved ? null : "Text angehängt, aber nicht gespeichert – bitte Speichern.";
	const merge = result.mergeError
		? `${result.text && saved ? "Text angehängt, " : ""}Audio konnte nicht zusammengeführt werden: ${result.mergeError}`
		: null;
	contNotice.value = [textNotice, merge].filter(Boolean).join(" ") || null;
}

async function finishContinue(): Promise<void> {
	if (!recording.value) return;
	await applyContinueResult(await cont.finish(recording.value));
}

async function retryContinue(): Promise<void> {
	if (!recording.value) return;
	await applyContinueResult(await cont.retry(recording.value));
}

// The new part lives only in this page (mic, hub job or a part kept for retry):
// leaving drops it, so ask first. Nuxt keys the page by id, so switching to
// another recording (desktop list) is a route update on this same record.
const CONTINUE_LEAVE = "Der neue Teil geht verloren. Trotzdem verlassen?";

function confirmLeave(): boolean {
	if (!contActive.value && !contRetained.value) return true;
	return window.confirm(CONTINUE_LEAVE);
}

onBeforeRouteLeave(() => confirmLeave());
onBeforeRouteUpdate((to, from) =>
	(to.params as { id?: string }).id === (from.params as { id?: string }).id || confirmLeave());

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

function goBack(): void {
	void navigateTo("/");
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
		void navigateTo("/");
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
});

onUnmounted(() => {
	stopPolling();
	audioDisposed = true;
	revokeAudio(audioSrc.value);
	audioSrc.value = null;
	window.removeEventListener("keydown", onKeyDown);
	window.removeEventListener("keyup", onKeyUp);
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
			<span
				class="detail__bar-title"
				data-testid="bar-title"
			>{{ recording?.title || "Aufnahme" }}</span>
			<div class="detail__actions">
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
						:disabled="restarting || contActive || contRetained"
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
					<div class="detail__toolbar">
						<div class="detail__tools">
							<button
								type="button"
								class="detail__ptt"
								:class="{
									'detail__ptt--active': pttState === 'recording',
									'detail__ptt--busy': pttState === 'transcribing',
								}"
								:disabled="pttState === 'transcribing' || contActive"
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
									pttState === "recording" ? "Aufnahme… loslassen zum Transkribieren"
									: pttState === "transcribing" ? "Transkribiert…"
										: `Diktieren (${pttKeyLabel} halten)`
								}}</span>
							</button>
							<button
								type="button"
								class="detail__ptt"
								:disabled="pttState !== 'idle' || contActive || contRetained"
								data-testid="continue-recording"
								@click="startContinue"
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
							<span
								v-if="contStartError"
								class="detail__tool-error"
								role="alert"
								data-testid="continue-error"
							>{{ contStartError }}</span>
						</div>
						<div class="detail__save">
							<span
								class="detail__save-state"
								:class="{ 'detail__save-state--dirty': dirty }"
								aria-live="polite"
								data-testid="save-state"
							>{{ dirty ? "Ungespeicherte Änderungen" : "Gespeichert" }}</span>
							<HButton
								variant="primary"
								:disabled="edit.saving.value || pttState !== 'idle' || contWorking || !dirty"
								data-testid="save-transcript"
								@click="saveTranscript"
							>
								{{ edit.saving.value ? "Speichert…" : "Speichern" }}
							</HButton>
						</div>
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
						progress text and the notice when they appear; Abbrechen stays outside. -->
					<div
						:class="contWorking
							? 'detail__banner detail__banner--progress'
							: contNotice ? 'detail__banner detail__banner--notice' : 'sr-only'"
					>
						<span
							v-if="contWorking"
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
								v-if="contWorking"
								data-testid="continue-banner"
							>{{
								cont.phase.value === "transcribing"
									? "Neuer Teil wird transkribiert – Seite bitte geöffnet lassen."
									: "Audio wird zusammengeführt…"
							}}</span>
							<span
								v-else-if="contNotice"
								data-testid="continue-notice"
							>{{ contNotice }}</span>
						</div>
						<HButton
							v-if="cont.phase.value === 'transcribing'"
							variant="secondary"
							data-testid="continue-cancel"
							@click="cont.cancel()"
						>
							Abbrechen
						</HButton>
					</div>

					<div
						v-if="contRetained"
						class="detail__banner"
						role="alert"
						data-testid="continue-banner"
					>
						<div class="detail__banner-text">
							{{ cont.error.value }}
							<span class="detail__banner-hint">Der neue Teil ist noch da – erneut versuchen oder verwerfen.</span>
						</div>
						<HButton
							variant="secondary"
							data-testid="continue-discard"
							@click="cont.discard()"
						>
							Verwerfen
						</HButton>
						<HButton
							variant="primary"
							data-testid="continue-retry"
							@click="retryContinue"
						>
							Erneut versuchen
						</HButton>
					</div>

					<div class="detail__surface">
						<TranscriptEditor
							ref="editorRef"
							v-model="edit.text.value"
							data-testid="transcript-edit"
							placeholder="Noch kein Transkript – hier tippen oder diktieren."
						/>
					</div>
				</template>
			</template>
		</div>

		<HPlayerDock
			v-if="recording?.audio_file && audioSrc"
			class="detail__dock"
			:src="audioSrc"
			:fallback-duration="recording.duration_seconds ?? undefined"
		/>

		<HRecordingSheet
			v-if="cont.phase.value === 'recording' && cont.recorder.recording.value"
			label="Weiter aufnehmen"
			:elapsed="cont.recorder.elapsed.value"
			:levels="cont.recorder.levels.value"
			:paused="cont.recorder.paused.value"
			@pause="cont.recorder.pause()"
			@resume="cont.recorder.resume()"
			@stop="finishContinue"
			@cancel="cont.cancel()"
		/>
	</div>
</template>

<style scoped>
/* Phone: the page owns the viewport (no shell header on this route). Desktop pane:
   the page fills the scrolling right pane (a flex column); the dock is sticky to it. */
.detail { min-height:100dvh; display:flex; flex-direction:column; background:var(--bg); }
.detail--pane { min-height:0; flex:1 0 auto; }
.detail__bar { position:sticky; top:0; z-index:5; display:flex; align-items:center; gap:8px; padding:10px 12px 10px 16px; background:var(--bg); border-bottom:1px solid var(--border); }
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
.detail__toolbar { display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap; }
.detail__tools { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.detail__tool-error { flex-basis:100%; color:var(--danger); font-family:var(--font-body); font-size:13px; }
.detail__save { display:flex; align-items:center; gap:12px; margin-left:auto; }
.detail__save-state { font-family:var(--font-body); font-size:13px; color:var(--fg3); }
.detail__save-state--dirty { color:var(--accent-ink,var(--accent)); font-weight:600; }
.detail__surface { flex:1; display:flex; flex-direction:column; min-height:240px; }
.detail__surface > :deep(*) { flex:1; }
.detail__ptt { display:inline-flex; align-items:center; gap:8px; padding:9px 16px; border-radius:999px; border:1.5px solid var(--border-strong); background:var(--white); color:var(--fg2); font-family:var(--font-body); font-weight:600; font-size:14px; cursor:pointer; touch-action:none; user-select:none; transition:background var(--motion-fast) var(--motion-ease), border-color var(--motion-fast) var(--motion-ease); }
.detail__ptt:hover:not(:disabled) { border-color:var(--accent); }
.detail__ptt--active { background:var(--accent); color:var(--fg-on-accent); border-color:var(--accent); }
.detail__ptt--busy { opacity:0.65; cursor:default; }
.detail__ptt:disabled:not(.detail__ptt--busy) { opacity:0.5; cursor:default; }
.detail__dock { position:sticky; bottom:0; z-index:5; }
</style>
