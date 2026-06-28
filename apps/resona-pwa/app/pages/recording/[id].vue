<script setup lang="ts">
import { deleteItem } from "@directus/sdk";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { isServiceUnreachable } from "#shared/errors";
import type { Transcript } from "~/types/directus";

const route = useRoute();
const id = String(route.params.id);

const { recording, transcript, loading, error, startPolling, stopPolling } = useRecording(id);
const { client, assetUrl } = useDirectus();

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

// Seed the editable text once the transcript first appears. Polling stops once
// the recording is terminal, so a single seed is enough; guard against
// clobbering the user's edits on a later refresh.
watch(transcript, (t) => {
	if (t && !seeded.value) {
		edit.text.value = t.text ?? "";
		savedText.value = t.text ?? "";
		seeded.value = true;
	}
}, { immediate: true });

const dirty = computed(() => seeded.value && edit.text.value !== savedText.value);

async function saveTranscript(): Promise<void> {
	if (!transcript.value) {
		return;
	}
	const target = edit.text.value;
	await edit.saveEdit(transcript.value.id, target);
	if (!edit.error.value) {
		savedText.value = target;
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
				<StatusChip :status="recording.status" />

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
				</div>

				<p
					v-else-if="recording.status === 'pending' || recording.status === 'transcribing'"
					class="detail__hint"
				>
					Transkribiert…
				</p>

				<template v-else-if="recording.status === 'done' && transcript">
					<textarea
						v-model="edit.text.value"
						class="detail__edit"
						data-testid="transcript-edit"
						spellcheck="false"
					/>
					<p
						v-if="edit.error.value"
						class="detail__error"
						data-testid="save-error"
					>
						{{ edit.error.value }}
					</p>
					<div class="detail__edit-actions">
						<HButton
							variant="primary"
							:disabled="edit.saving.value || !dirty"
							data-testid="save-transcript"
							@click="saveTranscript"
						>
							{{ edit.saving.value ? "Speichert…" : "Speichern" }}
						</HButton>
					</div>
				</template>

				<p
					v-else
					class="detail__hint"
				>
					Kein Transkript verfügbar.
				</p>
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
.detail__banner-text { display:flex; flex-direction:column; gap:4px; }
.detail__banner-hint { font-size:13px; opacity:0.85; }
.detail__transcript { white-space:pre-wrap; font-family:var(--font-body); font-size:16px; line-height:1.6; color:var(--fg1); margin:0; }
.detail__edit { width:100%; min-height:240px; box-sizing:border-box; resize:vertical; white-space:pre-wrap; font-family:var(--font-body); font-size:16px; line-height:1.6; color:var(--fg1); background:var(--surface,transparent); border:1.5px solid var(--border-strong); border-radius:var(--r-md); padding:12px 14px; }
.detail__edit:focus { outline:none; border-color:var(--accent); }
.detail__edit-actions { display:flex; justify-content:flex-end; }
</style>
