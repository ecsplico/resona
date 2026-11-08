<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { resolveAudioMime } from "~/utils/audioFile";

// Landing page for the Web Share Target. The service worker
// (public/share-target-handler.js) has already handled the POST: audio shares
// are stashed in the Cache API and redirected here as ?kind=audio; text shares
// are sent straight to /live and never reach this page. We read the stashed
// blob back and hand it to the same SaveSheet → transcribe flow as a normal
// upload.

const SHARE_CACHE = "resona-share-target-v1";
const SHARE_BLOB_KEY = "/share-target/audio";

const { saveRecording } = useUpload();
const hubSettings = useHubSettings();
const settingsDrawer = useSettingsDrawer();
const gate = useHubTokenGate();

const status = ref<"loading" | "blocked" | "ready" | "empty" | "error">("loading");
const pendingBlob = ref<Blob | null>(null);
const pendingTitle = ref("");
const pendingName = ref("");

async function loadStash(): Promise<void> {
	if (!("caches" in window)) {
		status.value = "empty";
		return;
	}
	try {
		const cache = await caches.open(SHARE_CACHE);
		const res = await cache.match(SHARE_BLOB_KEY);
		if (!res) {
			status.value = "empty";
			return;
		}
		// One-shot: drop the stash so a refresh doesn't re-import the same file.
		await cache.delete(SHARE_BLOB_KEY);

		const raw = await res.blob();
		const filename = decodeURIComponent(res.headers.get("x-share-filename") || "");
		const sharedTitle = decodeURIComponent(res.headers.get("x-share-title") || "");
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
		// Never empty: Speichern is disabled without a title.
		pendingTitle.value = (sharedTitle || filename.replace(/\.[^.]+$/, "")).trim()
			|| `Geteilte Aufnahme ${new Date().toLocaleString("de-DE")}`;
		status.value = "ready";
	}
	catch {
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
			<p>{{ status === "error" ? "Die geteilte Datei konnte nicht gelesen werden." : "Keine geteilte Aufnahme gefunden." }}</p>
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
