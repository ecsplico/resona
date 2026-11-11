<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { isNavigationFailure } from "vue-router";
import type { Recording, Transcript } from "~/types/directus";
import type { QueuedItem } from "~/composables/useOfflineQueue";
import type { RowAction } from "~/components/RecordingListItem.vue";
import { humanizeError } from "#shared/errors";
import type { SaveRequest } from "~/data/types";
import {
	claimUnfinished,
	discardUnfinished,
	listUnfinished,
	loadUnfinished,
	releaseUnfinished,
	type UnfinishedRecording,
} from "~/data/unfinishedRecordings";

const { items, pending, error, list, refresh, remove } = useRecordings();
// Records into IndexedDB as it goes, so a killed page can offer the audio again.
const recorder = useRecorder({ persist: () => ({ kind: "record" }) });
const { saveRecording } = useUpload();
const { copy, toText, downloadMarkdown } = useExport();
const store = useDataStore();
const queue = useOfflineQueue();
const queued = ref<QueuedItem[]>([]);
const settings = useSettingsDrawer();
const hubSettings = useHubSettings();
const gate = useHubTokenGate();
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

// Filters live in shared state so they survive the pane being remounted on phones
// (list -> detail -> back).
const search = useState("recordings-search", () => "");
const since = useState("recordings-since", () => "");

// Record flow state: record button -> HRecordingSheet (recording) -> SaveSheet (review).
const showRecorder = ref(false);
const recordError = ref<string | null>(null);
const showSave = ref(false);
const pendingBlob = ref<Blob | null>(null);
const pendingDuration = ref(0);
const pendingTitle = ref("");
// Persisted recorder session behind `pendingBlob`; deleted once the audio is saved or discarded.
const pendingSession = ref<string | null>(null);
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

// Switching between recordings in the desktop workspace swaps the history entry,
// so back from a recording always leaves the workspace instead of stepping through
// every recording opened before. From the list (no recording open) it stacks.
function openRoute(path: string) {
	return selectedId.value === null ? navigateTo(path) : navigateTo(path, { replace: true });
}

function openRecording(recording: Recording): void {
	void openRoute(`/recording/${recording.id}`);
}

// The list doesn't carry transcripts, so the row actions fetch this recording's
// transcript on demand. Untranscribed rows have nothing to fetch.
async function transcriptOf(recording: Recording): Promise<Transcript | null> {
	if (recording.status !== "done") return null;
	try {
		return await store.getTranscript(recording.id);
	}
	catch {
		return null;
	}
}

/** Title + text (same shape as the detail page), or only the title without a transcript. */
async function shareTextOf(recording: Recording): Promise<string> {
	const transcript = await transcriptOf(recording);
	return transcript ? toText(recording, transcript) : recording.title;
}

// Short confirmation for row actions that have no visible result of their own.
const actionNotice = ref<string | null>(null);
let noticeTimer: ReturnType<typeof setTimeout> | null = null;

function notify(message: string): void {
	actionNotice.value = message;
	if (noticeTimer) clearTimeout(noticeTimer);
	noticeTimer = setTimeout(() => {
		actionNotice.value = null;
		noticeTimer = null;
	}, 4000);
}

async function copyRecording(recording: Recording): Promise<void> {
	void copy(await shareTextOf(recording));
}

async function copyForShare(text: string, message: string): Promise<void> {
	try {
		await copy(text);
		notify(message);
	}
	catch {
		notify("Teilen ist hier nicht möglich.");
	}
}

// iOS only honours `navigator.share` called synchronously inside the tap, so
// "Teilen" cannot await the transcript. Opening the row menu prefetches the
// share text of a transcribed recording into this cache (by recording id);
// untranscribed rows share their title, which needs no fetch. Entries go when
// the row leaves the list or stops being done, on an explicit list refresh
// (another view changed a recording) and when the recording is deleted.
const shareTexts = new Map<string, string>();
const shareFetches = new Map<string, Promise<void>>();
let shareGeneration = 0;

function prefetchShareText(recording: Recording): void {
	if (recording.status !== "done" || shareTexts.has(recording.id) || shareFetches.has(recording.id)) return;
	const generation = shareGeneration;
	const request = shareTextOf(recording).then((text) => {
		if (generation === shareGeneration) shareTexts.set(recording.id, text);
	}).finally(() => {
		if (shareFetches.get(recording.id) === request) shareFetches.delete(recording.id);
	});
	shareFetches.set(recording.id, request);
}

function clearShareTexts(): void {
	shareGeneration++;
	shareTexts.clear();
	shareFetches.clear();
}

watch(items, (rows) => {
	const done = new Set(rows.filter(r => r.status === "done").map(r => r.id));
	for (const id of shareTexts.keys()) {
		if (!done.has(id)) shareTexts.delete(id);
	}
});

function shareRecording(recording: Recording): void {
	const text = recording.status === "done" ? shareTexts.get(recording.id) : recording.title;
	if (text === undefined) {
		prefetchShareText(recording);
		notify("Wird vorbereitet … bitte erneut tippen.");
		return;
	}
	if (typeof navigator === "undefined" || typeof navigator.share !== "function") {
		void copyForShare(text, "Teilen wird nicht unterstützt – Text kopiert.");
		return;
	}
	navigator.share({ title: recording.title || "Aufnahme", text }).catch((err: unknown) => {
		if (err instanceof Error && err.name === "AbortError") return;
		// e.g. NotAllowedError when the browser no longer counts the tap as user activation
		void copyForShare(text, "Teilen nicht möglich – Text kopiert.");
	});
}

async function exportRecording(recording: Recording): Promise<void> {
	downloadMarkdown(recording, await transcriptOf(recording));
}

// Delete: the menu asks, this dialog confirms, then the row goes through the data store.
const deleteTarget = ref<Recording | null>(null);
const deleting = ref(false);
const deleteError = ref<string | null>(null);
const deleteDialog = ref<HTMLElement | null>(null);
let focusBeforeDelete: HTMLElement | null = null;

async function askDelete(recording: Recording): Promise<void> {
	focusBeforeDelete = typeof document !== "undefined" ? document.activeElement as HTMLElement | null : null;
	deleteError.value = null;
	deleteTarget.value = recording;
	await nextTick();
	deleteDialog.value?.querySelector<HTMLElement>("[data-testid=\"cancel-delete\"]")?.focus();
}

const redictationRequest = useRedictationRequest();

async function redictate(recording: Recording): Promise<void> {
	redictationRequest.value = recording.id;
	const failure = await openRoute(`/recording/${recording.id}`);
	// Navigation blocked (unsaved edits kept): the request must not start a later visit.
	if (isNavigationFailure(failure) && redictationRequest.value === recording.id) {
		redictationRequest.value = null;
	}
}

const rowActions: Record<RowAction, (recording: Recording) => void> = {
	share: shareRecording,
	export: recording => void exportRecording(recording),
	redictate: recording => void redictate(recording),
	delete: recording => void askDelete(recording),
};

function onRowAction(recording: Recording, action: RowAction): void {
	rowActions[action](recording);
}

function closeDeleteDialog(): void {
	deleteTarget.value = null;
	deleteError.value = null;
	focusBeforeDelete?.focus();
	focusBeforeDelete = null;
}

function cancelDelete(): void {
	if (deleting.value) return;
	closeDeleteDialog();
}

// The dialog is modal (`aria-modal`): Tab and Shift+Tab cycle through its enabled buttons.
function trapDeleteFocus(event: KeyboardEvent): void {
	const focusable = Array.from(deleteDialog.value?.querySelectorAll<HTMLElement>("button:not([disabled])") ?? []);
	if (focusable.length === 0) {
		event.preventDefault();
		return;
	}
	const first = focusable[0]!;
	const last = focusable[focusable.length - 1]!;
	const active = document.activeElement as HTMLElement | null;
	const inside = active !== null && focusable.includes(active);
	if (event.shiftKey && (!inside || active === first)) {
		event.preventDefault();
		last.focus();
	}
	else if (!event.shiftKey && (!inside || active === last)) {
		event.preventDefault();
		first.focus();
	}
}

async function confirmDelete(): Promise<void> {
	const recording = deleteTarget.value;
	if (!recording || deleting.value) return;
	deleting.value = true;
	deleteError.value = null;
	try {
		// Desktop: the recording may be open in the right pane. Leave it first, so its
		// leave guard (unsaved edits) can still run and veto; a vetoed or failed
		// navigation cancels the delete.
		if (selectedId.value === recording.id) {
			const result = await navigateTo("/", { replace: true });
			if (result === false || isNavigationFailure(result)) {
				deleting.value = false;
				closeDeleteDialog();
				return;
			}
		}
		await remove(recording.id);
		shareTexts.delete(recording.id);
		deleteTarget.value = null;
		focusBeforeDelete = null;
	}
	catch (err) {
		deleteError.value = humanizeError(err, "Aufnahme konnte nicht gelöscht werden.");
	}
	finally {
		deleting.value = false;
	}
}

async function startRecording(): Promise<void> {
	if (!gate.allowed()) {
		recordError.value = gate.message;
		return;
	}
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
	const session = recorder.sessionId.value;
	const blob = await recorder.stop();
	pendingBlob.value = blob;
	pendingSession.value = session;
	pendingTitle.value = "";
	showRecorder.value = false;
	showSave.value = true;
}

/** Deletes the persisted copy of `pendingBlob` (saved for good, or thrown away). */
function dropPendingSession(): void {
	const session = pendingSession.value;
	pendingSession.value = null;
	if (session) void discardUnfinished(session).then(refreshUnfinished);
}

function triggerUpload(): void {
	if (!gate.allowed()) {
		recordError.value = gate.message;
		return;
	}
	recordError.value = null;
	uploadInput.value?.click();
}

function onUpload(event: Event): void {
	const input = event.target as HTMLInputElement;
	const file = input.files?.[0];
	if (!file) {
		return;
	}
	pendingBlob.value = file;
	pendingSession.value = null;
	pendingDuration.value = 0;
	pendingTitle.value = file.name.replace(/\.[^.]+$/, "");
	showSave.value = true;
	input.value = "";
}

async function cancelRecording(): Promise<void> {
	const session = recorder.sessionId.value;
	await recorder.stop();
	if (session) void discardUnfinished(session);
	pendingBlob.value = null;
	showRecorder.value = false;
}

async function onSave(payload: SaveRequest): Promise<void> {
	const blob = pendingBlob.value;
	if (!blob) {
		return;
	}
	const created = await saveRecording({
		blob,
		title: payload.title,
		titleAuto: payload.titleAuto,
		language: payload.language,
		profile: payload.profile,
		transcribe: payload.transcribe,
		duration_seconds: pendingDuration.value,
	});
	pendingBlob.value = null;
	showSave.value = false;
	dropPendingSession();
	await refresh();
	await refreshQueued();
	// Desktop: open the new recording in the right pane next to the list.
	if (wide.value) {
		await openRoute(`/recording/${created.id}`);
	}
}

function onDiscard(): void {
	pendingBlob.value = null;
	showSave.value = false;
	dropPendingSession();
}

// Recordings a killed or crashed page left in IndexedDB: offered one at a time.
const unfinished = ref<UnfinishedRecording[]>([]);
const unfinishedBusy = ref(false);
const unfinishedError = ref<string | null>(null);
const offeredUnfinished = computed(() => (showSave.value || showRecorder.value ? null : unfinished.value[0] ?? null));

async function refreshUnfinished(): Promise<void> {
	unfinished.value = await listUnfinished();
}

function formatUnfinished(meta: UnfinishedRecording): string {
	const date = new Date(meta.startedAt);
	const when = Number.isNaN(date.getTime())
		? ""
		: ` vom ${date.toLocaleDateString("de-DE")} ${date.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}`;
	const total = Math.max(0, Math.round(meta.elapsed));
	const length = `${Math.floor(total / 60)}:${(total % 60).toString().padStart(2, "0")}`;
	const what = meta.label ? `„${meta.label}“` : "Aufnahme";
	return `Nicht gespeichert: ${what}${when} (${length} min).`;
}

/** Opens the save sheet with the recovered audio; it is deleted from IndexedDB once saved or discarded there. */
async function keepUnfinished(meta: UnfinishedRecording): Promise<void> {
	if (unfinishedBusy.value) return;
	unfinishedBusy.value = true;
	unfinishedError.value = null;
	try {
		const recovered = await loadUnfinished(meta.id);
		if (!recovered) {
			// Nothing was written before the page went away.
			await discardUnfinished(meta.id);
			await refreshUnfinished();
			return;
		}
		// This page holds it now, so the offer goes away (and other tabs do not offer it).
		claimUnfinished(meta.id);
		unfinished.value = unfinished.value.filter(m => m.id !== meta.id);
		pendingBlob.value = recovered.blob;
		pendingDuration.value = recovered.seconds;
		pendingTitle.value = meta.label ?? "";
		pendingSession.value = meta.id;
		showSave.value = true;
	}
	catch (err) {
		unfinishedError.value = humanizeError(err, "Die Aufnahme konnte nicht geladen werden.");
	}
	finally {
		unfinishedBusy.value = false;
	}
}

async function discardOffered(meta: UnfinishedRecording): Promise<void> {
	unfinishedError.value = null;
	await discardUnfinished(meta.id);
	await refreshUnfinished();
}

// Other views (the detail page) ask for a reload after changing a recording.
watch(useRecordingsRefresh().signal, () => {
	clearShareTexts();
	void refresh();
});

onBeforeUnmount(() => {
	// A kept session still waiting in the save sheet becomes recoverable again.
	if (pendingSession.value) releaseUnfinished(pendingSession.value);
	if (noticeTimer) {
		clearTimeout(noticeTimer);
		noticeTimer = null;
	}
	if (pollTimer) {
		clearInterval(pollTimer);
		pollTimer = null;
	}
});

onMounted(() => {
	void list({ search: search.value, since: since.value });
	void refreshUnfinished();
	void refreshQueued();
	startPolling();
	// Drain the offline queue to Directus whenever connectivity returns; refresh
	// both lists after each successful upload so the row moves to the server list.
	queue.startAutoSync(async (item: QueuedItem) => {
		const created = await saveRecording({
			blob: item.blob,
			title: item.title,
			titleAuto: item.titleAuto ?? false,
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
			v-if="offeredUnfinished"
			class="pane__notice"
			role="region"
			aria-label="Nicht gespeicherte Aufnahme"
			data-testid="unfinished-recording"
		>
			<p class="pane__notice-text">
				{{ formatUnfinished(offeredUnfinished) }}
				<span
					v-if="unfinishedError"
					role="alert"
					data-testid="unfinished-error"
				>{{ unfinishedError }}</span>
			</p>
			<button
				class="pane__notice-action"
				type="button"
				:disabled="unfinishedBusy"
				data-testid="unfinished-discard"
				@click="discardOffered(offeredUnfinished)"
			>
				Verwerfen
			</button>
			<button
				class="pane__notice-action"
				type="button"
				:disabled="unfinishedBusy"
				data-testid="unfinished-keep"
				@click="keepUnfinished(offeredUnfinished)"
			>
				Behalten
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

			<p
				class="pane__status"
				:class="{ 'pane__status--idle': !actionNotice }"
				role="status"
				aria-live="polite"
				data-testid="action-notice"
			>
				{{ actionNotice }}
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
					@menu-open="prefetchShareText(recording)"
					@action="onRowAction(recording, $event)"
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
			:interruption="recorder.interruption.value"
			@pause="recorder.pause()"
			@resume="recorder.resume()"
			@continue="recorder.continueRecording()"
			@stop="stopRecording"
			@cancel="cancelRecording"
		/>

		<SaveSheet
			v-if="showSave"
			:initial-title="pendingTitle"
			@save="onSave"
			@discard="onDiscard"
		/>

		<div
			v-if="deleteTarget"
			ref="deleteDialog"
			class="confirm"
			role="alertdialog"
			aria-modal="true"
			aria-labelledby="delete-dialog-title"
			aria-describedby="delete-dialog-text"
			data-testid="delete-dialog"
			@keydown.esc="cancelDelete"
			@keydown.tab="trapDeleteFocus"
		>
			<div
				class="confirm__scrim"
				@click="cancelDelete"
			/>
			<div class="confirm__box">
				<p
					id="delete-dialog-title"
					class="confirm__title"
				>
					Aufnahme löschen?
				</p>
				<p
					id="delete-dialog-text"
					class="confirm__text"
				>
					„{{ deleteTarget.title || "Aufnahme" }}“ wird mit dem Transkript gelöscht. Das lässt sich nicht rückgängig machen.
				</p>
				<p
					v-if="deleteError"
					class="pane__error"
					data-testid="delete-error"
				>
					{{ deleteError }}
				</p>
				<div class="confirm__actions">
					<HButton
						variant="secondary"
						:disabled="deleting"
						data-testid="cancel-delete"
						@click="cancelDelete"
					>
						Abbrechen
					</HButton>
					<HButton
						class="confirm__delete"
						variant="primary"
						:disabled="deleting"
						data-testid="confirm-delete"
						@click="confirmDelete"
					>
						{{ deleting ? "Wird gelöscht…" : "Löschen" }}
					</HButton>
				</div>
			</div>
		</div>
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
.pane__status { margin:0; padding:10px 14px; border-radius:var(--r-md); background:var(--bg-sunken); color:var(--fg1); font-size:var(--fs-sm); }
/* Stays in the DOM (live region) but takes no room while there is nothing to say. */
.pane__status--idle { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0, 0, 0, 0); }

/* Delete confirmation: centred card over a scrim, above the dock. */
.confirm { position:fixed; inset:0; z-index:50; display:flex; align-items:center; justify-content:center; padding:24px; }
.confirm__scrim { position:absolute; inset:0; background:rgba(35,25,17,0.72); }
.confirm__box { position:relative; width:100%; max-width:420px; display:flex; flex-direction:column; gap:12px; padding:24px; border-radius:var(--r-lg); background:var(--white); box-shadow:var(--shadow-deep); }
.confirm__title { margin:0; font-family:var(--font-display); font-size:var(--fs-lg); font-weight:600; color:var(--fg1); }
.confirm__text { margin:0; font-size:var(--fs-md); line-height:1.5; color:var(--fg2); overflow-wrap:anywhere; }
.confirm__actions { display:flex; justify-content:flex-end; gap:8px; margin-top:4px; }
.confirm__actions .confirm__delete { background:var(--danger); }
</style>
