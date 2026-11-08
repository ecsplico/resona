<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import type { ResonaDocument } from "~/types/directus";
import { humanizeError } from "#shared/errors";

const store = useDataStore();
// Shared state: rows and search survive the detail round trip (see LIST_STATE_KEYS).
const items = useState<ResonaDocument[]>("documents-items", () => []);
const pending = ref(false);
const error = ref<string | null>(null);
const uploading = ref(false);
const search = useState("documents-search", () => "");
const uploadInput = ref<HTMLInputElement | null>(null);

const ACCEPT = "application/pdf,image/png,image/jpeg,image/tiff,.pdf,.png,.jpg,.jpeg,.tif,.tiff";

async function list(): Promise<void> {
	error.value = null;
	try {
		items.value = await store.listDocuments({ search: search.value });
	}
	catch (err) {
		error.value = humanizeError(err, "Dokumente konnten nicht geladen werden.");
	}
}

async function initialLoad(): Promise<void> {
	pending.value = true;
	try {
		await list();
	}
	finally {
		pending.value = false;
	}
}

function applyFilters(): void {
	void list();
}

function chipStatus(status: ResonaDocument["status"]): "pending" | "transcribing" | "done" | "error" {
	return status === "processing" ? "transcribing" : status;
}

function formatDate(value?: string): string {
	return value ? new Date(value).toLocaleString("de-DE") : "";
}

// Documents are processed in the background (worker or local processor flips
// them to done/error). Poll while any row is still in flight so the list updates
// itself the moment processing finishes, then go quiet again.
const POLL_MS = 4000;
let pollTimer: ReturnType<typeof setInterval> | null = null;

function hasActiveDocument(): boolean {
	return items.value.some(d => d.status === "pending" || d.status === "processing");
}

function startPolling(): void {
	if (pollTimer) {
		return;
	}
	pollTimer = setInterval(() => {
		if (hasActiveDocument()) {
			void list();
		}
	}, POLL_MS);
}

function openDocument(doc: ResonaDocument): void {
	void navigateTo(`/documents/${doc.id}`);
}

function triggerUpload(): void {
	const gate = useHubTokenGate();
	if (!gate.allowed()) {
		error.value = gate.message;
		return;
	}
	error.value = null;
	uploadInput.value?.click();
}

async function onUpload(event: Event): Promise<void> {
	const input = event.target as HTMLInputElement;
	const file = input.files?.[0];
	if (!file) {
		return;
	}
	input.value = "";
	uploading.value = true;
	error.value = null;
	try {
		await store.createDocument({ title: file.name.replace(/\.[^.]+$/, "") }, file, file.name);
		await list();
	}
	catch (err) {
		error.value = humanizeError(err, "Dokument konnte nicht hochgeladen werden.");
	}
	finally {
		uploading.value = false;
	}
}

onBeforeUnmount(() => {
	if (pollTimer) {
		clearInterval(pollTimer);
		pollTimer = null;
	}
});

onMounted(() => {
	void initialLoad();
	startPolling();
});
</script>

<template>
	<div class="docs">
		<h1 class="sr-only">
			Dokumente
		</h1>

		<div
			class="docs__toolbar"
			role="search"
		>
			<label class="docs__search">
				<span class="sr-only">Suchen</span>
				<svg
					class="docs__search-icon"
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
						cx="11"
						cy="11"
						r="7"
					/>
					<line
						x1="21"
						y1="21"
						x2="16.65"
						y2="16.65"
					/>
				</svg>
				<input
					v-model="search"
					class="docs__search-input"
					type="search"
					placeholder="Suchen"
					enterkeyhint="search"
					data-testid="docs-search"
					@input="applyFilters"
				>
			</label>
			<HButton
				class="docs__upload"
				variant="primary"
				:disabled="uploading"
				data-testid="docs-upload"
				@click="triggerUpload"
			>
				Dokument hochladen
			</HButton>
			<input
				ref="uploadInput"
				class="docs__upload-input"
				type="file"
				:accept="ACCEPT"
				data-testid="docs-upload-input"
				@change="onUpload"
			>
		</div>

		<div class="docs__body">
			<p
				v-if="error"
				class="docs__error"
				data-testid="docs-error"
			>
				{{ error }}
			</p>

			<div
				v-if="pending && items.length === 0"
				class="h-list"
				aria-busy="true"
				aria-label="Dokumente werden geladen"
				data-testid="docs-skeleton"
			>
				<div
					v-for="n in 3"
					:key="n"
					class="docs__skeleton"
				>
					<span class="docs__skeleton-bar docs__skeleton-bar--title" />
					<span class="docs__skeleton-bar docs__skeleton-bar--meta" />
				</div>
			</div>

			<div
				v-else-if="items.length > 0"
				class="h-list"
			>
				<HListRow
					v-for="doc in items"
					:key="doc.id"
					:title="doc.title"
					:meta="formatDate(doc.date_created)"
					:status="chipStatus(doc.status)"
					:data-testid="`doc-row-${doc.id}`"
					@select="openDocument(doc)"
				/>
			</div>

			<p
				v-else-if="!error && search"
				class="docs__empty"
				data-testid="docs-empty"
			>
				Keine Dokumente zu „{{ search }}“.
			</p>

			<p
				v-else-if="!error"
				class="docs__empty"
				data-testid="docs-empty"
			>
				Noch keine Dokumente – lade ein PDF oder ein Foto hoch, Resona macht Text daraus.
			</p>
		</div>
	</div>
</template>

<style scoped>
/* Full width under the shell header; the list sits in a centered column. */
.docs { width:100%; max-width:960px; margin:0 auto; padding-bottom:40px; }

/* Toolbar: search and upload in one row; the button matches the field height. */
.docs__toolbar { display:flex; gap:8px; padding:16px 16px 12px; }
.docs__search { flex:1; min-width:0; display:flex; align-items:center; gap:8px; height:44px; padding:0 12px; border:1.5px solid var(--border-strong); border-radius:var(--r-sm); background:var(--white); transition:border-color var(--motion-fast) var(--motion-ease), box-shadow var(--motion-fast) var(--motion-ease); }
.docs__search:hover { border-color:var(--crust); }
.docs__search:focus-within { border-color:var(--accent); box-shadow:0 0 0 3px var(--focus-ring); }
.docs__search-icon { flex-shrink:0; color:var(--fg3); }
.docs__search-input { flex:1; width:100%; min-width:0; border:none; background:transparent; padding:0; font-family:var(--font-body); font-size:var(--fs-md); color:var(--fg1); outline:none; }
.docs__search-input:focus-visible { outline:none; }
.docs__search-input::placeholder { color:var(--fg3); opacity:1; }
.docs__search-input::-webkit-search-decoration { appearance:none; }
.docs__toolbar .docs__upload { flex-shrink:0; height:44px; padding:0 16px; border-radius:var(--r-sm); font-size:var(--fs-sm); white-space:nowrap; transition:background-color var(--motion-fast) var(--motion-ease); }
.docs__toolbar .docs__upload:hover:not(:disabled) { background:var(--accent-hover); }
.docs__toolbar .docs__upload:active:not(:disabled) { transform:scale(0.98); }
.docs__upload-input { display:none; }

.docs__body { display:flex; flex-direction:column; gap:12px; padding:0 16px; }
.docs__error { margin:0; color:var(--danger); font-size:var(--fs-sm); }
.docs__empty { margin:0; padding:40px 24px; text-align:center; color:var(--fg2); font-size:var(--fs-md); line-height:1.5; text-wrap:balance; }

.docs__skeleton { display:flex; flex-direction:column; gap:8px; padding:16px; }
.docs__skeleton-bar { display:block; height:12px; border-radius:var(--r-xs); background:var(--cream-deep); animation:docs-pulse 1.4s ease-in-out infinite; }
.docs__skeleton-bar--title { width:55%; height:16px; }
.docs__skeleton-bar--meta { width:35%; }
@keyframes docs-pulse { 50% { opacity:0.5; } }
@media (prefers-reduced-motion:reduce) { .docs__skeleton-bar { animation:none; } }

@media (min-width:960px) {
	.docs__toolbar { padding:24px 24px 16px; }
	.docs__body { padding:0 24px; }
}
</style>
