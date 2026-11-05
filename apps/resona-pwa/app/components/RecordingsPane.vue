<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { Recording, Transcript } from "~/types/directus";
import type { QueuedItem } from "~/composables/useOfflineQueue";

const { items, pending, error, list, refresh } = useRecordings();
const recorder = useRecorder();
const { saveRecording } = useUpload();
const { copy, toText } = useExport();
const store = useDataStore();
const queue = useOfflineQueue();
const queued = ref<QueuedItem[]>([]);
const settings = useSettingsDrawer();
const hubSettings = useHubSettings();
const { wide, paneInLayout } = useShell();
const route = useRoute();

// In the desktop workspace the open recording is highlighted in the list.
const selectedId = computed(() => {
	const match = /^\/recording\/([^/]+)/.exec(route.path);
	return match ? decodeURIComponent(match[1]!) : null;
});

async function refreshQueued(): Promise<void> {
	queued.value = await queue.pending();
}

const search = ref("");
const since = ref("");

// Record flow state: record button -> HRecordingSheet (recording) -> SaveSheet (review).
const showRecorder = ref(false);
const recordError = ref<string | null>(null);
const showSave = ref(false);
const pendingBlob = ref<Blob | null>(null);
const pendingDuration = ref(0);
const pendingTitle = ref("");
const uploadInput = ref<HTMLInputElement | null>(null);

function applyFilters(): void {
	void list({ search: search.value, since: since.value });
}

// Batch recordings transcribe in the background (the worker flips them to
// done/error). Poll while any row is still in flight so the list updates itself
// the moment a transcription finishes, then go quiet again.
const POLL_MS = 4000;
let pollTimer: ReturnType<typeof setInterval> | null = null;

function hasActiveRecording(): boolean {
	return items.value.some(r => r.status === "pending" || r.status === "transcribing");
}

function startPolling(): void {
	if (pollTimer) {
		return;
	}
	pollTimer = setInterval(() => {
		if (hasActiveRecording()) {
			void list({ search: search.value, since: since.value });
		}
	}, POLL_MS);
}

function openRecording(recording: Recording): void {
	void navigateTo(`/recording/${recording.id}`);
}

async function copyRecording(recording: Recording): Promise<void> {
	// The list doesn't carry transcripts, so fetch this recording's transcript on
	// demand and copy title + text (same shape as the detail page). Untranscribed
	// rows have nothing to fetch — fall back to the title.
	let transcript: Transcript | null = null;
	if (recording.status === "done") {
		try {
			transcript = await store.getTranscript(recording.id);
		}
		catch {
			// fall through: copy title-only below
		}
	}
	void copy(transcript ? toText(recording, transcript) : recording.title);
}

async function startRecording(): Promise<void> {
	recordError.value = null;
	showRecorder.value = true;
	try {
		await recorder.start();
	}
	catch (err) {
		// Without this the sheet stays open on a dead recorder and "Speichern"
		// uploads an empty file.
		showRecorder.value = false;
		recordError.value = err instanceof Error ? err.message : String(err);
	}
}

async function stopRecording(): Promise<void> {
	pendingDuration.value = recorder.elapsed.value;
	const blob = await recorder.stop();
	pendingBlob.value = blob;
	pendingTitle.value = "";
	showRecorder.value = false;
	showSave.value = true;
}

function triggerUpload(): void {
	uploadInput.value?.click();
}

function onUpload(event: Event): void {
	const input = event.target as HTMLInputElement;
	const file = input.files?.[0];
	if (!file) {
		return;
	}
	pendingBlob.value = file;
	pendingDuration.value = 0;
	pendingTitle.value = file.name.replace(/\.[^.]+$/, "");
	showSave.value = true;
	input.value = "";
}

async function cancelRecording(): Promise<void> {
	await recorder.stop();
	pendingBlob.value = null;
	showRecorder.value = false;
}

async function onSave(payload: { title: string; language: string; profile: string; transcribe: boolean }): Promise<void> {
	const blob = pendingBlob.value;
	if (!blob) {
		return;
	}
	const created = await saveRecording({
		blob,
		title: payload.title,
		language: payload.language,
		profile: payload.profile,
		transcribe: payload.transcribe,
		duration_seconds: pendingDuration.value,
	});
	pendingBlob.value = null;
	showSave.value = false;
	await refresh();
	await refreshQueued();
	// Desktop: open the new recording in the right pane next to the list.
	if (wide.value) {
		await navigateTo(`/recording/${created.id}`);
	}
}

function onDiscard(): void {
	pendingBlob.value = null;
	showSave.value = false;
}

// Other views (the detail page) ask for a reload after changing a recording.
watch(useRecordingsRefresh().signal, () => {
	void refresh();
});

onBeforeUnmount(() => {
	if (pollTimer) {
		clearInterval(pollTimer);
		pollTimer = null;
	}
});

onMounted(() => {
	void list();
	void refreshQueued();
	startPolling();
	// Drain the offline queue to Directus whenever connectivity returns; refresh
	// both lists after each successful upload so the row moves to the server list.
	queue.startAutoSync(async (item: QueuedItem) => {
		const created = await saveRecording({
			blob: item.blob,
			title: item.title,
			language: item.language,
			profile: item.profile,
			transcribe: item.transcribe ?? true,
			duration_seconds: item.duration_seconds ?? 0,
		});
		await refresh();
		await refreshQueued();
		return created;
	});
});
</script>

<template>
	<div
		class="pane"
		:class="{ 'pane--aside': paneInLayout }"
	>
		<div
			v-if="!hubSettings.token"
			class="pane__notice"
			data-testid="hub-token-missing"
		>
			<p class="pane__notice-text">
				Kein Hub-Token – Transkription, Diktat und Live brauchen einen Token.
			</p>
			<button
				class="pane__notice-action"
				type="button"
				data-testid="hub-token-open-settings"
				@click="settings.show()"
			>
				Token eintragen
			</button>
		</div>

		<div
			class="pane__filters"
			role="search"
		>
			<label class="pane__search">
				<span class="sr-only">Suchen</span>
				<svg
					class="pane__search-icon"
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
					class="pane__search-input"
					type="search"
					placeholder="Suchen"
					enterkeyhint="search"
					data-testid="search"
					@input="applyFilters"
				>
			</label>
			<label class="pane__since">
				<span
					class="pane__since-prefix"
					aria-hidden="true"
				>ab</span>
				<span class="sr-only">ab Datum</span>
				<input
					v-model="since"
					class="pane__since-input"
					type="date"
					data-testid="since"
					@change="applyFilters"
				>
			</label>
		</div>

		<div class="pane__body">
			<p
				v-if="recordError"
				class="pane__error"
				data-testid="record-error"
			>
				{{ recordError }}
			</p>

			<p
				v-if="error"
				class="pane__error"
			>
				{{ error }}
			</p>

			<div
				v-if="queued.length > 0"
				class="h-list"
				data-testid="queued-list"
			>
				<QueuedListItem
					v-for="item in queued"
					:key="item.id"
					:item="item"
				/>
			</div>

			<div
				v-if="pending && items.length === 0"
				class="h-list"
				aria-busy="true"
				aria-label="Aufnahmen werden geladen"
				data-testid="list-skeleton"
			>
				<div
					v-for="n in 3"
					:key="n"
					class="pane__skeleton"
					data-testid="skeleton-row"
				>
					<span class="pane__skeleton-bar pane__skeleton-bar--title" />
					<span class="pane__skeleton-bar pane__skeleton-bar--meta" />
				</div>
			</div>

			<p
				v-else-if="items.length === 0"
				class="pane__empty"
				data-testid="list-empty"
			>
				Noch keine Aufnahmen – nimm etwas auf oder lade eine Datei hoch.
			</p>

			<div
				v-else
				class="h-list"
				data-testid="recordings-list"
			>
				<RecordingListItem
					v-for="recording in items"
					:key="recording.id"
					:recording="recording"
					:selected="recording.id === selectedId"
					@open="openRecording(recording)"
					@copy="void copyRecording(recording)"
				/>
			</div>
		</div>

		<nav
			class="dock"
			aria-label="Neue Aufnahme"
			data-testid="capture-dock"
		>
			<div class="dock__inner">
				<button
					class="dock__action"
					type="button"
					title="Audiodatei hochladen"
					data-testid="upload-trigger"
					@click="triggerUpload"
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
						<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
						<polyline points="17 8 12 3 7 8" />
						<line
							x1="12"
							y1="3"
							x2="12"
							y2="15"
						/>
					</svg>
					<span class="dock__label">Hochladen</span>
				</button>

				<button
					class="dock__action dock__action--record"
					type="button"
					data-testid="record-fab"
					@click="startRecording"
				>
					<span class="dock__record">
						<svg
							width="28"
							height="28"
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
								y2="22"
							/>
						</svg>
					</span>
					<span class="dock__label">Aufnehmen</span>
				</button>

				<NuxtLink
					class="dock__action"
					to="/live"
					data-testid="nav-live"
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
						<polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
					</svg>
					<span class="dock__label">Live</span>
				</NuxtLink>
			</div>
		</nav>

		<input
			ref="uploadInput"
			class="pane__upload-input"
			type="file"
			accept="audio/*"
			data-testid="upload-input"
			@change="onUpload"
		>

		<HRecordingSheet
			v-if="showRecorder"
			:elapsed="recorder.elapsed.value"
			:levels="recorder.levels.value"
			:paused="recorder.paused.value"
			@pause="recorder.pause()"
			@resume="recorder.resume()"
			@stop="stopRecording"
			@cancel="cancelRecording"
		/>

		<SaveSheet
			v-if="showSave"
			:initial-title="pendingTitle"
			@save="onSave"
			@discard="onDiscard"
		/>
	</div>
</template>

<style scoped>
/* Bottom padding = dock height + safe area, so the dock never covers the last row. */
.pane { --dock-h:84px; width:100%; max-width:960px; margin:0 auto; padding-bottom:calc(var(--dock-h) + env(safe-area-inset-bottom) + 24px); }
/* Desktop aside: the pane fills the aside's height and the dock sticks to its bottom
   edge instead of the viewport's, so it never covers the right pane. */
.pane--aside { display:flex; flex-direction:column; min-height:100%; max-width:none; padding-bottom:0; }
.pane--aside .pane__body { flex:1; padding-bottom:24px; }
.pane--aside .dock { position:sticky; background:var(--bg-panel); padding-bottom:0; }

.pane__notice { display:flex; align-items:center; flex-wrap:wrap; gap:8px 12px; margin:12px 16px 0; padding:12px 14px; border-radius:var(--r-md); background:var(--butter-soft); color:var(--butter-ink); font-size:var(--fs-sm); line-height:1.5; }
.pane__notice-text { flex:1 1 200px; margin:0; }
.pane__notice-action { flex-shrink:0; min-height:36px; padding:6px 12px; border:1.5px solid currentColor; border-radius:var(--r-sm); background:none; color:inherit; font-family:var(--font-body); font-size:var(--fs-sm); font-weight:600; cursor:pointer; transition:background-color var(--motion-fast) var(--motion-ease); }
.pane__notice-action:hover { background:rgba(255,255,255,0.45); }
.pane__notice-action:active { background:rgba(255,255,255,0.7); }

/* Filters: one row at every width; the search takes what the date field leaves. */
.pane__filters { display:flex; gap:8px; padding:16px 16px 12px; }
.pane__search, .pane__since { display:flex; align-items:center; gap:8px; height:44px; padding:0 12px; border:1.5px solid var(--border-strong); border-radius:var(--r-sm); background:var(--white); transition:border-color var(--motion-fast) var(--motion-ease), box-shadow var(--motion-fast) var(--motion-ease); }
.pane__search:hover, .pane__since:hover { border-color:var(--crust); }
.pane__search:focus-within, .pane__since:focus-within { border-color:var(--accent); box-shadow:0 0 0 3px var(--focus-ring); }
.pane__search { flex:1; min-width:0; }
.pane__search-icon { flex-shrink:0; color:var(--fg3); }
.pane__search-input, .pane__since-input { min-width:0; border:none; background:transparent; padding:0; font-family:var(--font-body); font-size:var(--fs-md); color:var(--fg1); outline:none; }
.pane__search-input:focus-visible, .pane__since-input:focus-visible { outline:none; }
.pane__search-input { flex:1; width:100%; }
.pane__search-input::placeholder { color:var(--fg3); opacity:1; }
.pane__search-input::-webkit-search-decoration { appearance:none; }
.pane__since { flex:0 0 auto; width:150px; padding:0 8px 0 10px; }
.pane__since-prefix { flex-shrink:0; font-size:var(--fs-sm); font-weight:600; color:var(--fg3); }
.pane__since-input { flex:1; width:100%; font-family:var(--font-mono); font-size:var(--fs-sm); font-variant-numeric:tabular-nums; }

.pane__body { display:flex; flex-direction:column; gap:12px; padding:0 16px; }
.pane__error { margin:0; color:var(--danger); font-size:var(--fs-sm); }
.pane__empty { margin:0; padding:40px 24px; text-align:center; color:var(--fg2); font-size:var(--fs-md); line-height:1.5; text-wrap:balance; }

.pane__skeleton { display:flex; flex-direction:column; gap:8px; padding:16px; }
.pane__skeleton-bar { display:block; height:12px; border-radius:var(--r-xs); background:var(--cream-deep); animation:pane-pulse 1.4s ease-in-out infinite; }
.pane__skeleton-bar--title { width:55%; height:16px; }
.pane__skeleton-bar--meta { width:35%; }
@keyframes pane-pulse { 50% { opacity:0.5; } }
@media (prefers-reduced-motion:reduce) { .pane__skeleton-bar { animation:none; } }

/* Capture dock: Hochladen · record · Live, fixed to the bottom on phones. */
.dock { position:fixed; left:0; right:0; bottom:0; z-index:15; background:var(--bg); border-top:1px solid var(--border); padding-bottom:env(safe-area-inset-bottom); }
.dock__inner { display:grid; grid-template-columns:1fr auto 1fr; align-items:end; max-width:560px; height:var(--dock-h); margin:0 auto; padding:0 16px 8px; }
.dock__action { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:4px; justify-self:center; min-width:72px; min-height:56px; padding:6px 10px; border:none; border-radius:var(--r-md); background:none; color:var(--fg2); font-family:var(--font-body); text-decoration:none; cursor:pointer; transition:background-color var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease); }
.dock__action:hover { background:var(--cream-deep); color:var(--fg1); }
.dock__action:active { background:var(--crust-soft); }
.dock__label { font-size:var(--fs-xs); font-weight:600; line-height:1.2; }
.dock__action--record { padding-top:0; color:var(--fg1); }
.dock__action--record:hover, .dock__action--record:active { background:none; }
.dock__record { display:flex; align-items:center; justify-content:center; width:64px; height:64px; margin-top:-28px; border-radius:50%; background:var(--accent); color:var(--fg-on-accent); box-shadow:var(--shadow-lift); transition:background-color var(--motion-fast) var(--motion-ease), transform var(--motion-fast) var(--motion-ease); }
.dock__action--record:hover .dock__record { background:var(--accent-hover); }
.dock__action--record:active .dock__record { transform:scale(0.96); }

.pane__upload-input { display:none; }
</style>
