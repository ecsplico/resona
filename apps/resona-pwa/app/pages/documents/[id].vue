<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import type { ResonaDocument } from "~/types/directus";
import { humanizeError } from "#shared/errors";

const route = useRoute();
const id = String(route.params.id);
const store = useDataStore();
const { copy, downloadDocumentMarkdown } = useExport();

const doc = ref<ResonaDocument | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);
const retrying = ref(false);
const confirmingDelete = ref(false);
const deleting = ref(false);

const chipStatus = computed(() => {
	const status = doc.value?.status ?? "pending";
	return status === "processing" ? "transcribing" : status;
});

function isTerminal(status: ResonaDocument["status"] | undefined): boolean {
	return status === "done" || status === "error";
}

async function refresh(): Promise<void> {
	loading.value = true;
	error.value = null;
	try {
		doc.value = await store.getDocument(id);
	}
	catch (err) {
		error.value = humanizeError(err, "Dokument konnte nicht geladen werden.");
	}
	finally {
		loading.value = false;
	}
}

// Same polling contract as useRecording: refresh once, then every 4 s until
// the document reaches done/error.
let timer: ReturnType<typeof setInterval> | null = null;
let starting = false;
// Set on unmount so an in-flight first refresh never arms the interval.
let disposed = false;

function stopPolling(): void {
	if (timer !== null) {
		clearInterval(timer);
		timer = null;
	}
}

function startPolling(intervalMs = 4000): void {
	if (timer !== null || starting) {
		return;
	}
	starting = true;
	void refresh().then(() => {
		starting = false;
		if (disposed || isTerminal(doc.value?.status)) {
			return;
		}
		timer = setInterval(() => {
			void refresh().then(() => {
				if (isTerminal(doc.value?.status)) {
					stopPolling();
				}
			});
		}, intervalMs);
	});
}

async function retry(): Promise<void> {
	retrying.value = true;
	try {
		await store.updateDocument(id, { status: "pending", error_message: null });
		stopPolling();
		startPolling();
	}
	catch (err) {
		error.value = humanizeError(err, "Dokument konnte nicht neu gestartet werden.");
	}
	finally {
		retrying.value = false;
	}
}

function copyMarkdown(): void {
	void copy(doc.value?.markdown ?? "");
}

function exportMd(): void {
	if (doc.value) {
		downloadDocumentMarkdown(doc.value);
	}
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
		await store.deleteDocument(id);
		void navigateTo("/documents");
	}
	catch (err) {
		error.value = humanizeError(err, "Dokument konnte nicht gelöscht werden.");
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
	disposed = true;
	stopPolling();
});
</script>

<template>
	<div class="doc">
		<HAppBar tone="light">
			<template #leading>
				<NuxtLink
					class="doc__back"
					to="/documents"
					data-testid="doc-back"
				>
					‹ Dokumente
				</NuxtLink>
			</template>
			<span
				class="doc__title"
				data-testid="doc-title"
			>{{ doc?.title ?? "" }}</span>
			<template #trailing>
				<StatusChip
					v-if="doc"
					:status="chipStatus"
				/>
			</template>
		</HAppBar>

		<div
			v-if="confirmingDelete"
			class="doc__confirm"
			data-testid="doc-confirm-bar"
		>
			<span>Dieses Dokument löschen?</span>
			<div class="doc__confirm-actions">
				<HButton
					variant="secondary"
					data-testid="doc-cancel-delete"
					@click="cancelDelete"
				>
					Abbrechen
				</HButton>
				<HButton
					variant="primary"
					:disabled="deleting"
					data-testid="doc-confirm-delete"
					@click="confirmDelete"
				>
					Löschen
				</HButton>
			</div>
		</div>

		<div class="doc__body">
			<p
				v-if="loading && !doc"
				class="doc__hint"
			>
				Lädt…
			</p>

			<p
				v-if="error"
				class="doc__error"
				data-testid="doc-load-error"
			>
				{{ error }}
			</p>

			<div
				v-if="doc?.status === 'error'"
				class="doc__banner"
				data-testid="doc-error-banner"
			>
				<span>{{ doc.error_message || "Verarbeitung fehlgeschlagen." }}</span>
				<HButton
					variant="secondary"
					:disabled="retrying"
					data-testid="doc-retry"
					@click="retry"
				>
					Erneut verarbeiten
				</HButton>
			</div>

			<template v-if="doc">
				<div class="doc__actions">
					<HButton
						variant="secondary"
						:disabled="!doc.markdown"
						data-testid="doc-copy"
						@click="copyMarkdown"
					>
						Kopieren
					</HButton>
					<HButton
						variant="secondary"
						:disabled="!doc.markdown"
						data-testid="doc-download"
						@click="exportMd"
					>
						Als .md laden
					</HButton>
					<HButton
						variant="secondary"
						data-testid="doc-delete"
						@click="askDelete"
					>
						Löschen
					</HButton>
				</div>

				<pre
					v-if="doc.markdown"
					class="doc__markdown"
					data-testid="doc-markdown"
				>{{ doc.markdown }}</pre>
				<p
					v-else-if="doc.status !== 'error'"
					class="doc__hint"
				>
					Wird verarbeitet…
				</p>
			</template>
		</div>
	</div>
</template>

<style scoped>
.doc { min-height:100vh; background:var(--bg); padding-bottom:40px; }
.doc__back { color:var(--accent); font-weight:600; font-size:15px; text-decoration:none; }
.doc__title { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.doc__confirm { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:12px 20px; background:var(--butter-soft); }
.doc__confirm-actions { display:flex; gap:8px; }
.doc__body { display:flex; flex-direction:column; gap:12px; padding:16px 20px; }
.doc__banner { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:12px 14px; border-radius:12px; background:var(--tomato-soft); color:var(--danger); font-size:14px; }
.doc__actions { display:flex; flex-wrap:wrap; gap:8px; }
.doc__markdown { white-space:pre-wrap; word-break:break-word; margin:0; padding:16px; border-radius:12px; background:var(--white); color:var(--fg1); font-family:var(--font-mono, monospace); font-size:14px; line-height:1.55; }
.doc__error { color:var(--danger); font-size:14px; margin:0; }
.doc__hint { color:var(--fg3); font-size:15px; margin:0; text-align:center; padding:32px 0; }
</style>
