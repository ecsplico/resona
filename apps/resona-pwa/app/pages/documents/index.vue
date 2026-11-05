<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import type { ResonaDocument } from "~/types/directus";
import { humanizeError } from "#shared/errors";

const store = useDataStore();
const items = ref<ResonaDocument[]>([]);
const pending = ref(false);
const error = ref<string | null>(null);
const uploading = ref(false);
const search = ref("");
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
		<HAppBar
			tone="light"
			title="Dokumente"
		>
			<template #leading>
				<NuxtLink
					class="docs__back"
					to="/"
					data-testid="docs-back"
				>
					‹ Zurück
				</NuxtLink>
			</template>
		</HAppBar>

		<div class="docs__actions">
			<HField
				v-model="search"
				class="docs__search"
				label="Suchen"
				data-testid="docs-search"
				@update:model-value="applyFilters"
			/>
			<HButton
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

		<div class="docs__list">
			<p
				v-if="error"
				class="docs__error"
				data-testid="docs-error"
			>
				{{ error }}
			</p>

			<p
				v-if="pending"
				class="docs__hint"
			>
				Lädt…
			</p>

			<p
				v-else-if="items.length === 0"
				class="docs__hint"
			>
				Noch keine Dokumente
			</p>

			<div
				v-if="items.length > 0"
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
		</div>
	</div>
</template>

<style scoped>
.docs { min-height:100vh; background:var(--bg); padding-bottom:40px; }
.docs__back { color:var(--accent); font-weight:600; font-size:15px; text-decoration:none; }
.docs__actions { display:flex; gap:12px; align-items:flex-end; padding:16px 20px 8px; }
.docs__search { flex:1; }
.docs__upload-input { display:none; }
.docs__list { display:flex; flex-direction:column; gap:12px; padding:8px 20px 20px; }
.docs__error { color:var(--danger); font-size:14px; margin:0; }
.docs__hint { color:var(--fg3); font-size:15px; margin:0; text-align:center; padding:32px 0; }
</style>
