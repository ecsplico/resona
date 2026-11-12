<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import type { SaveRequest } from "~/data/types";
import { resolveAudioMime } from "~/utils/audioFile";
import { documentMime, SHARE_CACHE, SHARE_STASH_KEYS, type ShareKind } from "~/utils/shareStash";
import { humanizeError } from "#shared/errors";

// Landing page for shared files (`utils/shareStash.ts`). Web Share Target: the
// service worker (public/share-target-handler.js) has already handled the POST:
// audio shares are stashed in the Cache API and redirected here as ?kind=audio;
// text shares are sent straight to /live and never reach this page. Capacitor
// app: `utils/nativeShare.ts` stashes the shared file the same way and opens
// ?kind=audio|document. Audio goes through the same SaveSheet → transcribe flow
// as a normal upload; a document is uploaded right away, as on the documents page.

// A second share (new `n`) while this page is open remounts it.
definePageMeta({ key: route => route.fullPath });

const { saveRecording } = useUpload();
const store = useDataStore();
const hubSettings = useHubSettings();
const settingsDrawer = useSettingsDrawer();
const gate = useHubTokenGate();

const status = ref<"loading" | "blocked" | "ready" | "empty" | "error">("loading");
const errorMessage = ref("");
const pendingBlob = ref<Blob | null>(null);
const pendingTitle = ref("");
const pendingName = ref("");

const kind: ShareKind = useRoute().query.kind === "document" ? "document" : "audio";

async function loadStash(): Promise<void> {
	if (!("caches" in window)) {
		status.value = "empty";
		return;
	}
	try {
		const cache = await caches.open(SHARE_CACHE);
		const key = SHARE_STASH_KEYS[kind];
		const res = await cache.match(key);
		if (!res) {
			status.value = "empty";
			return;
		}
		// One-shot: drop the stash so a refresh doesn't re-import the same file.
		await cache.delete(key);

		const raw = await res.blob();
		const filename = decodeURIComponent(res.headers.get("x-share-filename") || "");
		const sharedTitle = decodeURIComponent(res.headers.get("x-share-title") || "");
		if (kind === "document") {
			await saveDocument(raw, filename, sharedTitle);
			return;
		}
		// The share POST often carries no or a generic type: derive it from the
		// filename and keep the name on the blob so the stored file stays playable.
		const type = resolveAudioMime(raw.type, filename);
		if (filename) {
			pendingBlob.value = new File([raw], filename, { type });
		}
		else {
			pendingBlob.value = type === raw.type ? raw : new Blob([raw], { type });
		}
		pendingName.value = filename;
		// May be empty: SaveSheet then saves a placeholder title that gets replaced after transcription.
		pendingTitle.value = (sharedTitle || filename.replace(/\.[^.]+$/, "")).trim();
		status.value = "ready";
	}
	catch {
		status.value = "error";
	}
}

async function saveDocument(raw: Blob, filename: string, sharedTitle: string): Promise<void> {
	const name = filename || "Dokument";
	const type = documentMime(raw.type, filename) ?? raw.type;
	const file = new File([raw], name, { type });
	try {
		const created = await store.createDocument({ title: (sharedTitle || name.replace(/\.[^.]+$/, "")).trim() }, file, name);
		await navigateTo(`/documents/${created.id}`, { replace: true });
	}
	catch (err) {
		errorMessage.value = humanizeError(err, "Dokument konnte nicht hochgeladen werden.");
		status.value = "error";
	}
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
	const route = useRoute();
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
		// 0 → useUpload reads the real length from the audio.
		duration_seconds: 0,
		sourceName: pendingName.value,
	});
	pendingBlob.value = null;
	await navigateTo(`/recording/${created.id}`, { replace: true });
}

function onDiscard(): void {
	pendingBlob.value = null;
	void navigateTo("/", { replace: true });
}
</script>

<template>
	<div class="share-target">
		<p
			v-if="status === 'loading'"
			class="share-target__note"
		>
			Geteilte Datei wird geladen …
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
			<p>{{ status === "error" ? errorMessage || "Die geteilte Datei konnte nicht gelesen werden." : "Keine geteilte Datei gefunden." }}</p>
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
			@save="onSave"
			@discard="onDiscard"
		/>
	</div>
</template>

<style scoped>
.share-target { min-height:60vh; display:flex; align-items:center; justify-content:center; padding:24px; }
.share-target__note { display:flex; flex-direction:column; gap:16px; align-items:center; text-align:center; color:var(--fg1); font-family:var(--font-body); }
</style>
