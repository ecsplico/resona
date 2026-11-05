<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import type { ResonaDocument } from "~/types/directus";
import { humanizeError } from "#shared/errors";

const route = useRoute();
const id = String(route.params.id);
const store = useDataStore();
const { copy, downloadDocumentMarkdown } = useExport();
const { wide } = useShell();

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

const metaLine = computed(() => {
	const d = doc.value;
	if (!d) {
		return "";
	}
	const parts: string[] = [];
	if (d.date_created) {
		parts.push(new Date(d.date_created).toLocaleString("de-DE"));
	}
	if (Array.isArray(d.pages) && d.pages.length > 0) {
		parts.push(d.pages.length === 1 ? "1 Seite" : `${d.pages.length} Seiten`);
	}
	return parts.join(" · ");
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
		<header class="doc__bar">
			<NuxtLink
				v-if="!wide"
				class="doc__icon"
				to="/documents"
				aria-label="Zurück"
				title="Zurück"
				data-testid="doc-back"
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
			</NuxtLink>
			<span
				class="doc__bar-title"
				data-testid="doc-bar-title"
			>{{ doc?.title || "Dokument" }}</span>
			<div
				v-if="doc"
				class="doc__actions"
			>
				<button
					type="button"
					class="doc__icon"
					aria-label="Kopieren"
					title="Kopieren"
					:disabled="!doc.markdown"
					data-testid="doc-copy"
					@click="copyMarkdown"
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
				<button
					type="button"
					class="doc__icon"
					aria-label="Als .md laden"
					title="Als .md laden"
					:disabled="!doc.markdown"
					data-testid="doc-download"
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
						<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
						<path d="M7 10l5 5 5-5" />
						<line
							x1="12"
							y1="15"
							x2="12"
							y2="3"
						/>
					</svg>
				</button>
				<button
					type="button"
					class="doc__icon doc__icon--danger"
					aria-label="Löschen"
					title="Löschen"
					data-testid="doc-delete"
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
			class="doc__confirm"
			role="alert"
			data-testid="doc-confirm-bar"
		>
			<span class="doc__confirm-text">Dieses Dokument löschen?</span>
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

			<template v-if="doc">
				<section class="doc__meta">
					<h1
						class="doc__title"
						data-testid="doc-title"
					>
						{{ doc.title }}
					</h1>
					<p
						v-if="metaLine"
						class="doc__metaline tabular"
						data-testid="doc-meta-line"
					>
						{{ metaLine }}
					</p>
					<div
						v-if="chipStatus !== 'done'"
						class="doc__state"
					>
						<StatusChip :status="chipStatus" />
					</div>
				</section>

				<div
					v-if="doc.status === 'error'"
					class="doc__banner"
					role="alert"
					data-testid="doc-error-banner"
				>
					<span class="doc__banner-text">{{ doc.error_message || "Verarbeitung fehlgeschlagen." }}</span>
					<HButton
						variant="secondary"
						:disabled="retrying"
						data-testid="doc-retry"
						@click="retry"
					>
						Erneut verarbeiten
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
/* Phone: the page owns the viewport (no shell header on this route) and brings its
   back bar. Wide: the shell header navigates; the bar keeps title and actions. */
.doc { flex:1; min-height:100dvh; display:flex; flex-direction:column; background:var(--bg); }
.doc__bar { position:sticky; top:0; z-index:5; display:flex; align-items:center; gap:8px; padding:10px 12px 10px 16px; padding-top:calc(10px + env(safe-area-inset-top)); background:var(--bg); border-bottom:1px solid var(--border); }
.doc__bar-title { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:var(--fs-md); font-weight:600; color:var(--fg1); }
.doc__actions { display:flex; align-items:center; gap:2px; }
.doc__icon { width:38px; height:38px; flex:0 0 auto; border:none; border-radius:50%; background:transparent; color:var(--crust-deep); display:inline-flex; align-items:center; justify-content:center; cursor:pointer; transition:background-color var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease); }
.doc__icon:hover:not(:disabled) { background:var(--cream-deep); }
.doc__icon:active:not(:disabled) { background:var(--crust-soft); }
.doc__icon:disabled { opacity:0.4; cursor:not-allowed; }
.doc__icon--danger:hover:not(:disabled) { background:var(--tomato-soft); color:var(--tomato-deep); }

.doc__confirm { display:flex; align-items:center; gap:12px; padding:12px 20px; background:var(--tomato-soft); }
.doc__confirm-text { flex:1; font-size:var(--fs-sm); font-weight:600; color:var(--tomato-deep); }
.doc__confirm-actions { display:flex; gap:8px; }

.doc__body { flex:1; width:100%; max-width:72ch; margin:0 auto; box-sizing:border-box; display:flex; flex-direction:column; gap:16px; padding:20px 20px 40px; }
.doc__meta { display:flex; flex-direction:column; gap:6px; }
.doc__title { margin:0; font-family:var(--font-display); font-size:var(--fs-xl); font-weight:600; line-height:1.25; color:var(--fg1); overflow-wrap:anywhere; }
.doc__metaline { margin:0; font-family:var(--font-mono); font-size:var(--fs-xs); color:var(--fg3); }
.doc__state { display:flex; align-items:center; gap:10px; margin-top:6px; }
.doc__banner { display:flex; align-items:center; flex-wrap:wrap; gap:12px; padding:12px 14px; border-radius:var(--r-md); background:var(--tomato-soft); color:var(--tomato-deep); font-size:var(--fs-sm); }
.doc__banner-text { flex:1; min-width:180px; }
.doc__markdown { white-space:pre-wrap; word-break:break-word; margin:0; padding:16px; border:1px solid var(--border); border-radius:var(--r-md); background:var(--white); color:var(--fg1); font-family:var(--font-mono); font-size:var(--fs-sm); line-height:1.55; }
.doc__error { color:var(--danger); font-size:var(--fs-sm); margin:0; }
.doc__hint { color:var(--fg3); font-size:var(--fs-md); margin:0; text-align:center; padding:32px 0; }

@media (min-width:960px) {
	.doc { min-height:0; }
	.doc__bar { position:static; padding:12px 24px; }
	.doc__body { padding:24px 24px 48px; }
}
</style>
