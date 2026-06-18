<script setup lang="ts">
import { deleteItem } from "@directus/sdk";
import { onMounted, onUnmounted, ref } from "vue";

const route = useRoute();
const id = String(route.params.id);

const { recording, transcript, loading, error, startPolling, stopPolling } = useRecording(id);
const { client, assetUrl } = useDirectus();

const confirmingDelete = ref(false);
const deleting = ref(false);

function goBack(): void {
	void navigateTo("/");
}

function copyTranscript(): void {
	void navigator.clipboard?.writeText(transcript.value?.text ?? "");
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
				>
					{{ recording.error_message ?? "Bei der Transkription ist ein Fehler aufgetreten." }}
				</div>

				<p
					v-else-if="recording.status === 'pending' || recording.status === 'transcribing'"
					class="detail__hint"
				>
					Transkribiert…
				</p>

				<p
					v-else-if="recording.status === 'done' && transcript"
					class="detail__transcript"
					v-text="transcript.text"
				/>

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
.detail__banner { background:var(--tomato-soft); color:var(--tomato-deep); border-radius:var(--r-md); padding:14px 16px; font-family:var(--font-body); font-size:14px; }
.detail__transcript { white-space:pre-wrap; font-family:var(--font-body); font-size:16px; line-height:1.6; color:var(--fg1); margin:0; }
</style>
