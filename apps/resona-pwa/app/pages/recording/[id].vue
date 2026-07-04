<script setup lang="ts">
import { deleteItem, updateItem } from "@directus/sdk";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { isServiceUnreachable } from "#shared/errors";
import type { Recording, Transcript } from "~/types/directus";

const route = useRoute();
const id = String(route.params.id);
const cfg = useRuntimeConfig().public;

const { recording, transcript, loading, error, startPolling, stopPolling } = useRecording(id);
const { client, assetUrl } = useDirectus();
const live = useLive();

// Distinguish a transient "service unreachable" failure (offline styling +
// retry hint) from a genuine transcription error (red banner).
const offlineError = computed(() => isServiceUnreachable(recording.value?.error_message));

const edit = useTranscriptEdit("");
const seeded = ref(false);
const savedText = ref("");

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
		edit.text.value = t.text ?? "";
		savedText.value = t.text ?? "";
		seeded.value = true;
	}
	else if (canEdit.value) {
		edit.text.value = "";
		savedText.value = "";
		seeded.value = true;
	}
}, { immediate: true });

const dirty = computed(() => seeded.value && edit.text.value !== savedText.value);

async function saveTranscript(): Promise<void> {
	const target = edit.text.value;
	if (transcript.value) {
		await edit.saveEdit(transcript.value.id, target);
		if (!edit.error.value) {
			savedText.value = target;
		}
		return;
	}
	// No transcript row yet -> create one for this recording.
	const created = await edit.createTranscript(id, target, cfg.defaultEngine);
	if (created) {
		transcript.value = created;
		savedText.value = target;
		seeded.value = true;
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
		await client.request(updateItem("recordings", id, { review_status: value }));
		recording.value.review_status = value;
	}
	finally {
		settingStatus.value = false;
	}
}

// ── Push-to-talk dictation (insert at cursor) ───────────────────────────────
const editorRef = ref<{ insertText: (text: string) => void; focus: () => void } | null>(null);
const dictating = ref(false);

async function startDictation(): Promise<void> {
	if (dictating.value) {
		return;
	}
	dictating.value = true;
	await live.start({ language: recording.value?.language, engine: cfg.defaultEngine });
}

function stopDictation(): void {
	if (!dictating.value) {
		return;
	}
	dictating.value = false;
	// Grab the final + trailing partial before tearing the socket down, then let
	// the editor splice it in at the caret (cursor ends after the insert).
	const dictated = live.fullText.value.trim();
	live.stop();
	if (dictated) {
		editorRef.value?.insertText(dictated);
	}
}

function onPttDown(e: PointerEvent): void {
	try {
		(e.currentTarget as Element | null)?.setPointerCapture?.(e.pointerId);
	}
	catch {
		// pointer capture unsupported — release/up handlers still work
	}
	void startDictation();
}

function onPttUp(e: PointerEvent): void {
	try {
		(e.currentTarget as Element | null)?.releasePointerCapture?.(e.pointerId);
	}
	catch {
		// ignore
	}
	stopDictation();
}

const restarting = ref(false);

async function restartTranscription(): Promise<void> {
	restarting.value = true;
	try {
		// Allow the editor to re-seed from the freshly produced transcript.
		seeded.value = false;
		await client.request(updateItem("recordings", id, { status: "pending", error_message: null }));
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
		await client.request(deleteItem("recordings", id));
		void navigateTo("/");
	}
	finally {
		deleting.value = false;
		confirmingDelete.value = false;
	}
}

onMounted(() => {
	startPolling();
});

onUnmounted(() => {
	stopPolling();
});
</script>

<template>
	<div class="detail">
		<HAppBar
			tone="light"
			:title="recording?.title ?? 'Aufnahme'"
		>
			<template #leading>
				<HIconButton
					tone="cream"
					aria-label="Zurück"
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
				</HIconButton>
			</template>
			<template #trailing>
				<HIconButton
					tone="cream"
					aria-label="Kopieren"
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
				</HIconButton>
				<HIconButton
					tone="cream"
					aria-label="Als TXT exportieren"
					data-testid="export-txt"
					@click="exportTxt"
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
						<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
						<polyline points="14 2 14 8 20 8" />
						<line
							x1="9"
							y1="13"
							x2="15"
							y2="13"
						/>
						<line
							x1="9"
							y1="17"
							x2="13"
							y2="17"
						/>
					</svg>
				</HIconButton>
				<HIconButton
					tone="cream"
					aria-label="Als Markdown exportieren"
					data-testid="export-md"
					@click="exportMd"
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
						<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
						<polyline points="14 2 14 8 20 8" />
						<polyline points="8 18 8 13 10 15 12 13 12 18" />
					</svg>
				</HIconButton>
				<HIconButton
					tone="cream"
					aria-label="Löschen"
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
				</HIconButton>
			</template>
		</HAppBar>

		<div
			v-if="confirmingDelete"
			class="detail__confirm"
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
				<div class="detail__chips">
					<StatusChip :status="recording.status" />
					<HChip
						v-if="canEdit"
						:tone="reviewStatus === 'ready' ? 'success' : 'neutral'"
						data-testid="review-chip"
					>
						{{ reviewStatus === "ready" ? "Freigegeben" : "In Bearbeitung" }}
					</HChip>
				</div>

				<div
					v-if="recording.status === 'error'"
					class="detail__banner"
					:class="{ 'detail__banner--offline': offlineError }"
					data-testid="error-banner"
				>
					<svg
						v-if="offlineError"
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
						<path d="M22.61 16.95A5 5 0 0 0 18 10h-1.26a8 8 0 0 0-7.05-6M5 5a8 8 0 0 0 4 15h9a5 5 0 0 0 1.7-.3" />
						<line
							x1="1"
							y1="1"
							x2="23"
							y2="23"
						/>
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
						:disabled="restarting"
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
					<div class="detail__dictate">
						<button
							type="button"
							class="detail__ptt"
							:class="{ 'detail__ptt--active': dictating }"
							data-testid="push-to-talk"
							:aria-pressed="dictating"
							aria-label="Zum Diktieren gedrückt halten"
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
							<span>{{ dictating ? "Diktiert… loslassen zum Einfügen" : "Diktieren (gedrückt halten)" }}</span>
						</button>
						<p
							v-if="dictating"
							class="detail__dictate-preview"
							data-testid="dictate-preview"
						>
							{{ live.fullText.value || "Sprich jetzt…" }}
						</p>
					</div>

					<TranscriptEditor
						ref="editorRef"
						v-model="edit.text.value"
						data-testid="transcript-edit"
						placeholder="Noch kein Transkript – hier tippen oder diktieren."
					/>
					<p
						v-if="edit.error.value"
						class="detail__error"
						data-testid="save-error"
					>
						{{ edit.error.value }}
					</p>
					<div class="detail__edit-actions">
						<div
							class="detail__review"
							role="group"
							aria-label="Status"
						>
							<HButton
								variant="secondary"
								:disabled="settingStatus || reviewStatus === 'editing'"
								data-testid="review-editing"
								@click="setReviewStatus('editing')"
							>
								In Bearbeitung
							</HButton>
							<HButton
								variant="secondary"
								:disabled="settingStatus || reviewStatus === 'ready'"
								data-testid="review-ready"
								@click="setReviewStatus('ready')"
							>
								Fertig
							</HButton>
						</div>
						<HButton
							variant="primary"
							:disabled="edit.saving.value || dictating || !dirty"
							data-testid="save-transcript"
							@click="saveTranscript"
						>
							{{ edit.saving.value ? "Speichert…" : "Speichern" }}
						</HButton>
					</div>
				</template>
			</template>
		</div>

		<HPlayerDock
			v-if="recording?.audio_file"
			:src="assetUrl(recording.audio_file)"
		/>
	</div>
</template>

<style scoped>
.detail { min-height:100vh; display:flex; flex-direction:column; background:var(--bg); }
.detail__confirm { display:flex; align-items:center; gap:12px; padding:12px 20px; background:var(--tomato-soft); }
.detail__confirm-text { flex:1; font-family:var(--font-body); font-size:14px; font-weight:600; color:var(--tomato-deep); }
.detail__confirm-actions { display:flex; gap:8px; }
.detail__body { flex:1; display:flex; flex-direction:column; gap:14px; padding:20px; }
.detail__hint { color:var(--fg3); font-size:15px; margin:0; }
.detail__error { color:var(--danger); font-size:14px; margin:0; }
.detail__banner { display:flex; align-items:flex-start; gap:10px; background:var(--tomato-soft); color:var(--tomato-deep); border-radius:var(--r-md); padding:14px 16px; font-family:var(--font-body); font-size:14px; }
.detail__banner--offline { background:var(--butter-soft); color:#8B6200; }
.detail__banner-icon { flex-shrink:0; margin-top:1px; }
.detail__banner-text { flex:1; display:flex; flex-direction:column; gap:4px; }
.detail__banner-hint { font-size:13px; opacity:0.85; }
.detail__transcript { white-space:pre-wrap; font-family:var(--font-body); font-size:16px; line-height:1.6; color:var(--fg1); margin:0; }
.detail__edit-actions { display:flex; align-items:center; gap:10px; flex-wrap:wrap; justify-content:flex-end; }
.detail__review { display:flex; gap:8px; margin-right:auto; }
.detail__chips { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.detail__dictate { display:flex; flex-direction:column; gap:8px; }
.detail__ptt { display:inline-flex; align-items:center; gap:8px; align-self:flex-start; padding:10px 16px; border-radius:999px; border:1.5px solid var(--border-strong); background:var(--surface,transparent); color:var(--fg2); font-family:var(--font-body); font-weight:600; font-size:14px; cursor:pointer; touch-action:none; user-select:none; }
.detail__ptt--active { background:var(--accent); color:var(--fg-on-accent); border-color:var(--accent); }
.detail__dictate-preview { margin:0; font-family:var(--font-body); font-size:14px; line-height:1.5; color:var(--fg3); font-style:italic; }
</style>
