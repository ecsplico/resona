<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import type { SaveRequest } from "~/data/types";
import { resolveAudioMime } from "~/utils/audioFile";
import { documentMime, takeShares, type SharedFile } from "~/utils/shareStash";
import { humanizeError } from "#shared/errors";

// Landing page for shared files (`utils/shareStash.ts`). Web Share Target: the
// service worker (public/share-target-handler.js) has already handled the POST:
// audio shares are stashed in the Cache API and redirected here as ?kind=audio;
// text shares are sent straight to /live and never reach this page. Capacitor
// app: `utils/nativeShare.ts` stashes every usable file of a share and opens
// ?count=n (&skipped=m). Documents are uploaded right away, as on the documents
// page; recordings then go through one SaveSheet → transcribe flow as a normal
// upload. One file opens its page; several open the list of their kind.

// A second share (new `n`) while this page is open remounts it.
definePageMeta({ key: route => route.fullPath });

const { saveRecording } = useUpload();
const store = useDataStore();
const hubSettings = useHubSettings();
const settingsDrawer = useSettingsDrawer();
const gate = useHubTokenGate();
const route = useRoute();

/** More would be a stash from somewhere else; the plugin keeps at most 50 copies. */
const MAX_SHARED_FILES = 50;

const status = ref<"loading" | "uploading" | "blocked" | "ready" | "empty" | "error">("loading");
const errorMessage = ref("");
const progress = ref("");
const audio = ref<SharedFile[]>([]);
const pendingTitle = ref("");

/** Files of this share that did not make it, with the reason; shown when all are done. */
const failures: string[] = [];
const skipped = Math.max(0, Number(route.query.skipped) || 0);
let total = 0;
let documents = 0;
let lastCreated = "";

function stem(filename: string): string {
	return filename.replace(/\.[^.]+$/, "").trim();
}

async function loadStash(): Promise<void> {
	if (!("caches" in window)) {
		status.value = "empty";
		return;
	}
	// `?count=` from the app, none from the web share target.
	const count = Number(route.query.count);
	let files: SharedFile[];
	try {
		files = await takeShares(Number.isInteger(count) && count > 0 ? Math.min(count, MAX_SHARED_FILES) : null);
	}
	catch {
		status.value = "error";
		return;
	}
	if (files.length === 0) {
		status.value = "empty";
		return;
	}
	total = files.length;
	const single = total === 1;
	const docs = files.filter(f => f.kind === "document");
	for (const [index, file] of docs.entries()) {
		status.value = "uploading";
		progress.value = docs.length > 1 ? `Dokument ${index + 1} von ${docs.length} wird hochgeladen ...` : "Dokument wird hochgeladen ...";
		await saveDocument(file, single);
	}
	audio.value = files.filter(f => f.kind === "audio");
	if (audio.value.length === 0) {
		await finish();
		return;
	}
	// May be empty: SaveSheet then saves a placeholder title that gets replaced after transcription.
	pendingTitle.value = audio.value.length === 1 ? (single && audio.value[0]!.title) || stem(audio.value[0]!.filename) : "";
	status.value = "ready";
}

async function saveDocument(file: SharedFile, single: boolean): Promise<void> {
	const name = file.filename || "Dokument";
	const type = documentMime(file.blob.type, file.filename) ?? file.blob.type;
	try {
		// The sender's subject names the document only when it is the only file.
		const title = ((single && file.title) || stem(name)).trim();
		const created = await store.createDocument({ title }, new File([file.blob], name, { type }), name);
		documents += 1;
		lastCreated = `/documents/${created.id}`;
	}
	catch (err) {
		failures.push(`${name}: ${humanizeError(err, "Dokument konnte nicht hochgeladen werden.")}`);
	}
}

/** The shared file as a playable recording: the share often carries no or a generic type. */
function audioBlob(file: SharedFile): Blob {
	const type = resolveAudioMime(file.blob.type, file.filename);
	if (file.filename) {
		return new File([file.blob], file.filename, { type });
	}
	return type === file.blob.type ? file.blob : new Blob([file.blob], { type });
}

async function onSave(payload: SaveRequest): Promise<void> {
	const files = audio.value;
	audio.value = [];
	const several = files.length > 1;
	status.value = "uploading";
	for (const [index, file] of files.entries()) {
		progress.value = several ? `Aufnahme ${index + 1} von ${files.length} wird gespeichert ...` : "Aufnahme wird gespeichert ...";
		// Several recordings have no title field: each keeps its file name.
		const name = several ? stem(file.filename) : "";
		try {
			const created = await saveRecording({
				blob: audioBlob(file),
				title: name || payload.title,
				titleAuto: name ? false : payload.titleAuto,
				language: payload.language,
				profile: payload.profile,
				transcribe: payload.transcribe,
				// 0 -> useUpload reads the real length from the audio.
				duration_seconds: 0,
				sourceName: file.filename,
			});
			lastCreated = `/recording/${created.id}`;
		}
		catch (err) {
			failures.push(`${file.filename || "Aufnahme"}: ${humanizeError(err, "Aufnahme konnte nicht gespeichert werden.")}`);
		}
	}
	await finish(files.length);
}

function onDiscard(): void {
	audio.value = [];
	void finish();
}

/** Opens what was created, or reports what was not. `recordings`: how many were saved or tried. */
async function finish(recordings = 0): Promise<void> {
	if (failures.length > 0 || skipped > 0) {
		const notTaken = skipped > 0 ? [`${skipped} ${skipped === 1 ? "Datei hat" : "Dateien haben"} ein Format, das Resona nicht übernimmt.`] : [];
		errorMessage.value = [...failures, ...notTaken].join("\n");
		status.value = "error";
		return;
	}
	if (total === 1 && lastCreated) {
		await navigateTo(lastCreated, { replace: true });
		return;
	}
	await navigateTo(recordings > 0 || documents === 0 ? "/" : "/documents", { replace: true });
}

// Without a hub token the stash stays untouched until the token is entered.
function start(): void {
	if (!gate.allowed()) {
		status.value = "blocked";
		return;
	}
	status.value = "loading";
	void loadStash();
}

onMounted(() => {
	if (route.query.error) {
		status.value = "error";
		return;
	}
	start();
});

// Entering the token while the share waits resumes the import.
watch(() => hubSettings.token, () => {
	if (status.value === "blocked") {
		start();
	}
});
</script>

<template>
	<div class="share-target">
		<p
			v-if="status === 'loading' || status === 'uploading'"
			class="share-target__note"
			data-testid="share-progress"
		>
			{{ status === "uploading" ? progress : "Geteilte Datei wird geladen ..." }}
		</p>
		<div
			v-else-if="status === 'blocked'"
			class="share-target__note"
			data-testid="share-token-missing"
		>
			<p>{{ gate.message }}</p>
			<HButton
				variant="primary"
				data-testid="share-open-settings"
				@click="settingsDrawer.show()"
			>
				Token eintragen
			</HButton>
		</div>
		<div
			v-else-if="status === 'empty' || status === 'error'"
			class="share-target__note"
		>
			<p
				class="share-target__message"
				data-testid="share-message"
			>
				{{ status === "error" ? errorMessage || "Die geteilte Datei konnte nicht gelesen werden." : "Keine geteilte Datei gefunden." }}
			</p>
			<HButton
				variant="primary"
				@click="navigateTo('/', { replace: true })"
			>
				Zur Übersicht
			</HButton>
		</div>

		<SaveSheet
			v-if="status === 'ready'"
			:initial-title="pendingTitle"
			:count="audio.length"
			@save="onSave"
			@discard="onDiscard"
		/>
	</div>
</template>

<style scoped>
.share-target { min-height:60vh; display:flex; align-items:center; justify-content:center; padding:24px; }
.share-target__note { display:flex; flex-direction:column; gap:16px; align-items:center; text-align:center; color:var(--fg1); font-family:var(--font-body); }
.share-target__message { white-space:pre-line; }
</style>
