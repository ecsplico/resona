<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import type { NewRecording } from "~/data/types";
import type { ListenEngine } from "~/composables/useHub";

const cfg = useRuntimeConfig().public;

// When launched from a calendar event (/live?title=…), pre-fill the recording
// name with the event's "<name> – <date>" title.
const route = useRoute();
const presetTitle = typeof route.query.title === "string" ? route.query.title : "";

const capture = usePcmCapture();
const hubLive = useLive();
const device = useDeviceSpeech();
const settings = useLiveSettings();
const source = computed(() => (settings.source === "device" ? device : hubLive));

const listenEngines = ref<ListenEngine[]>([]);
const listenDefault = ref<string | null>(null);
const listenLoaded = ref(false);
const selectedListenEngine = computed(() => {
	const name = settings.hubEngine || listenDefault.value;
	return listenEngines.value.find(e => e.name === name);
});
const noHubLive = computed(() =>
	settings.source === "hub" && listenLoaded.value && listenEngines.value.length === 0,
);

const recorder = useRecorder();
const store = useDataStore();
const { copy } = useExport();

const sessionActive = ref(false);
const showTitlePrompt = ref(false);
const saving = ref(false);
const saveError = ref<string | null>(null);
const title = ref(presetTitle);
const capturedBlob = ref<Blob | null>(null);
const capturedDuration = ref(0);
const capturedText = ref("");
const capturedEngine = ref("");

// Chunked correction by a better hub model (Settings → Live).
const correction = useLiveCorrection();
const correctionActive = ref(false);
let correctionDone: Promise<void> | null = null;
let unsubscribeFinal: (() => void) | null = null;
const correctionDefault = ref<string | null>(null);
const correctionPending = computed(() => (correctionActive.value ? correction.pending.value : 0));

const rollingText = computed(() => {
	const f = correctionActive.value ? correction.text.value : source.value.final.value;
	const p = source.value.partial.value;
	if (f && p) {
		return `${f} ${p}`;
	}
	return f || p;
});

function copyTranscript(): void {
	if (!rollingText.value) {
		return;
	}
	void copy(rollingText.value);
}

const sourceError = computed(() => source.value.error.value);
// Show the reconnect banner only mid-session (after start, before a deliberate
// stop) when the socket drops and no source error explains it.
const showReconnect = computed(() =>
	sessionActive.value && !sourceError.value && source.value.status.value === "CLOSED",
);

async function loadListenEngines(): Promise<void> {
	try {
		const res = await useHub().listListenEngines();
		listenEngines.value = res.engines;
		listenDefault.value = res.default;
	}
	catch {
		// unreachable hub: treat as offering nothing
		listenEngines.value = [];
		listenDefault.value = null;
	}
	listenLoaded.value = true;
}

async function loadCorrectionEngines(): Promise<void> {
	try {
		correctionDefault.value = (await useHub().listEngines()).default;
	}
	catch {
		// unreachable hub: fall back to the generic "hub" stamp
		correctionDefault.value = null;
	}
}

function startCorrection(language: string): void {
	correctionActive.value = settings.correction;
	if (!settings.correction) {
		return;
	}
	correction.reset();
	correction.start({
		capture,
		language,
		engine: settings.correctionEngine,
		chunkSeconds: settings.chunkSeconds,
	});
	unsubscribeFinal?.();
	unsubscribeFinal = source.value.onFinal(t => correction.addFinal(t));
}

async function startSource(language: string): Promise<void> {
	if (settings.source === "device") {
		try {
			await device.start({ language });
		}
		catch (err) {
			device.error.value = err instanceof Error ? err.message : "Lokale Spracherkennung nicht verfügbar";
		}
		return;
	}
	await hubLive.start({
		language,
		engine: settings.hubEngine || undefined,
		private: selectedListenEngine.value?.private === false ? false : undefined,
		capture,
	});
}

async function startLive(): Promise<void> {
	if (sessionActive.value || noHubLive.value) {
		return;
	}
	sessionActive.value = true;

	const language = cfg.defaultLanguage;

	// Acquire the mic ONCE and share the single stream with both the live
	// transcriber and the parallel recorder so we don't open the mic twice.
	const media = (globalThis as { navigator?: Navigator }).navigator?.mediaDevices;
	if (media?.getUserMedia) {
		let stream: MediaStream | undefined;
		try {
			stream = await media.getUserMedia({ audio: true });
		}
		catch {
			// no mic permission — fall through to transcript-only mode
		}
		if (stream) {
			await capture.start(stream);
			startCorrection(language);
			await startSource(language);
			try {
				await recorder.start(stream);
			}
			catch {
				// recorder unsupported (e.g. Safari without webm) — live transcription
				// continues; audio will not be saved.
			}
			return;
		}
	}

	// No mic (tests / SSR / permission denied): start transcript path without audio.
	startCorrection(language);
	await startSource(language);
}

async function stopLive(): Promise<void> {
	sessionActive.value = false;
	capturedDuration.value = recorder.elapsed.value;
	capturedBlob.value = await recorder.stop();
	// Snapshot AFTER the recorder.stop() await: yielding to the microtask queue
	// lets Vue flush any pending reactive updates from WebSocket messages (e.g.
	// the last partial that arrived just before the user pressed stop).
	// Snapshot BEFORE source.stop(), which removes the data watcher immediately.
	capturedText.value = source.value.fullText.value.trim();
	const liveLabel = settings.source === "device"
		? "web-speech"
		: settings.hubEngine || listenDefault.value || "hub-live";
	capturedEngine.value = liveLabel;
	correctionDone = null;
	if (correctionActive.value) {
		// Close the open block and drain the queue before the capture stops.
		const engineLabel = `${liveLabel}+${settings.correctionEngine || correctionDefault.value || "hub"}`;
		// The unfinished partial never becomes a final; feed it in so it isn't dropped.
		const pendingPartial = source.value.partial.value.trim();
		if (pendingPartial) {
			correction.addFinal(pendingPartial);
		}
		correctionDone = correction.finish().then(() => {
			capturedText.value = correction.text.value;
			if (correction.blocks.value.some(b => b.state === "corrected")) {
				capturedEngine.value = engineLabel;
			}
		});
	}
	source.value.stop();
	unsubscribeFinal?.();
	unsubscribeFinal = null;
	capture.stop();
	title.value = presetTitle;
	showTitlePrompt.value = true;
}

async function confirmSave(): Promise<void> {
	if (saving.value) {
		return;
	}
	saving.value = true;
	saveError.value = null;
	try {
		if (correctionDone) {
			await correctionDone;
		}
		const blob = capturedBlob.value && capturedBlob.value.size > 0 ? capturedBlob.value : null;
		const input: NewRecording = {
			title: title.value,
			language: cfg.defaultLanguage,
			profile: cfg.defaultProfile,
			source: "live",
			status: "done",
			duration_seconds: capturedDuration.value,
		};
		const recording = await store.createRecording(input, blob, `${title.value}.webm`);

		await store.createTranscript(recording.id, { text: capturedText.value, engine: capturedEngine.value });

		void navigateTo("/");
	}
	catch {
		// Keep the title prompt, captured blob and transcript so the user can retry.
		saveError.value = "Speichern fehlgeschlagen. Bitte erneut versuchen.";
	}
	finally {
		saving.value = false;
	}
}

function cancelSave(): void {
	showTitlePrompt.value = false;
	void navigateTo("/");
}

onMounted(async () => {
	if (settings.correction) {
		await Promise.all([loadListenEngines(), loadCorrectionEngines()]);
	}
	else {
		await loadListenEngines();
	}
	await startLive();
});
</script>

<template>
	<div class="live">
		<header class="live__bar">
			<NuxtLink
				class="live__back"
				to="/"
				data-testid="live-back"
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
					<polyline points="15 18 9 12 15 6" />
				</svg>
				Zurück
			</NuxtLink>
			<h1 class="live__title">
				Live
			</h1>
		</header>

		<div
			v-if="sessionActive && sourceError"
			class="live__reconnect"
			data-testid="live-source-error"
			role="alert"
		>
			{{ sourceError }}
		</div>
		<div
			v-else-if="showReconnect"
			class="live__reconnect"
			data-testid="reconnect-banner"
		>
			Verbindung unterbrochen – Wiederverbindung…
		</div>

		<div class="live__body">
			<div class="live__column">
				<div
					v-if="rollingText"
					class="live__toolbar"
				>
					<HIconButton
						tone="glass"
						aria-label="Kopieren"
						title="Kopieren"
						data-testid="copy-live"
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
				</div>
				<p
					class="live__transcript"
					data-testid="transcript"
				>
					<template v-if="correctionActive">
						<span
							v-for="(block, i) in correction.blocks.value"
							:key="block.id"
							class="live__final live__block"
							:class="{
								'live__block--corrected': block.state === 'corrected',
								'live__block--pending': block.state === 'correcting' || block.state === 'queued',
							}"
							data-testid="live-block"
						>{{ i > 0 ? " " : "" }}{{ block.correctedText ?? block.liveText }}</span>
					</template>
					<span
						v-else
						class="live__final"
					>{{ source.final.value }}</span>
					<span
						v-if="source.partial.value"
						class="live__partial"
					>{{ (correctionActive ? correction.text.value : source.final.value) ? " " : "" }}{{ source.partial.value }}</span>
					<span
						v-if="!rollingText"
						class="live__hint"
					>Sprich jetzt…</span>
				</p>
			</div>
		</div>

		<p
			v-if="noHubLive"
			class="live__notice"
			data-testid="live-no-hub-stream"
		>
			Dieser Hub bietet kein Live-Streaming an – in Einstellungen → Live die lokale Erkennung wählen.
		</p>

		<div
			v-if="!showTitlePrompt && !sessionActive"
			class="live__controls"
		>
			<button
				class="live__stop"
				type="button"
				data-testid="start-live"
				aria-label="Aufnahme starten"
				:disabled="noHubLive || !listenLoaded"
				@click="startLive"
			>
				<svg
					width="26"
					height="26"
					viewBox="0 0 24 24"
					fill="currentColor"
					aria-hidden="true"
				>
					<circle
						cx="12"
						cy="12"
						r="7"
					/>
				</svg>
			</button>
		</div>

		<div
			v-if="!showTitlePrompt && sessionActive"
			class="live__controls"
		>
			<button
				class="live__stop"
				type="button"
				data-testid="stop-live"
				aria-label="Aufnahme beenden"
				@click="stopLive"
			>
				<svg
					width="26"
					height="26"
					viewBox="0 0 24 24"
					fill="currentColor"
					aria-hidden="true"
				>
					<rect
						x="6"
						y="6"
						width="12"
						height="12"
						rx="2"
					/>
				</svg>
			</button>
		</div>

		<div
			v-if="showTitlePrompt"
			class="live__save"
			data-testid="save-prompt"
		>
			<HField
				v-model="title"
				class="live__title"
				label="Titel"
				data-testid="title-input"
			/>
			<p
				v-if="correctionPending > 0"
				class="live__pending"
				data-testid="live-correction-pending"
			>
				Korrektur läuft… ({{ correctionPending }} Abschnitte)
			</p>
			<p
				v-if="saveError"
				class="live__error"
				data-testid="live-save-error"
			>
				{{ saveError }}
			</p>
			<div class="live__save-actions">
				<HButton
					variant="secondary"
					data-testid="cancel-save"
					@click="cancelSave"
				>
					Verwerfen
				</HButton>
				<HButton
					variant="primary"
					:disabled="saving"
					data-testid="confirm-save"
					@click="confirmSave"
				>
					Speichern
				</HButton>
			</div>
		</div>
	</div>
</template>

<style scoped>
/* The surface goes dark whenever the microphone is live. It fills the shell's page
   container (flex column); on phones the shell header is hidden on /live. */
.live { flex:1; min-height:100dvh; display:flex; flex-direction:column; background:var(--night); color:var(--on-night); color-scheme:dark; }
@media (min-width:960px) { .live { min-height:0; } }
.live :deep(:focus-visible) { outline-color:var(--tomato); }

.live__bar { display:grid; grid-template-columns:1fr auto 1fr; align-items:center; min-height:56px; padding:env(safe-area-inset-top) 8px 0; border-bottom:1px solid color-mix(in srgb, var(--on-night) 10%, transparent); }
.live__back { justify-self:start; display:inline-flex; align-items:center; gap:4px; min-height:40px; padding:0 12px 0 6px; border-radius:var(--r-full); color:var(--on-night); font-size:var(--fs-sm); font-weight:600; text-decoration:none; transition:background-color var(--motion-fast) var(--motion-ease); }
.live__back:hover { background:var(--night-raised); }
.live__back:active { background:color-mix(in srgb, var(--on-night) 14%, transparent); }
.live__title { margin:0; font-family:var(--font-display); font-size:var(--fs-lg); font-weight:600; line-height:1.2; color:var(--on-night); }

.live__reconnect { background:var(--tomato-deep); color:var(--white); padding:10px 20px; font-size:var(--fs-sm); font-weight:600; text-align:center; }
.live__body { flex:1; padding:24px 20px; overflow-y:auto; }
.live__column { max-width:72ch; margin:0 auto; }
.live__toolbar { display:flex; justify-content:flex-end; margin-bottom:12px; }
.live__transcript { white-space:pre-wrap; font-size:20px; line-height:1.6; margin:0; }
.live__final, .live__block--corrected { color:var(--on-night); }
.live__partial, .live__block--pending, .live__hint { color:var(--on-night-mute); }
.live__hint { font-style:italic; }

.live__notice { width:calc(100% - 40px); max-width:72ch; margin:0 auto 12px; padding:12px 14px; border:1px solid color-mix(in srgb, var(--warning) 45%, transparent); border-radius:var(--r-md); background:var(--night-raised); color:var(--on-night); font-size:var(--fs-sm); line-height:1.5; }

.live__controls { display:flex; justify-content:center; padding:28px 0 calc(40px + env(safe-area-inset-bottom)); }
.live__stop { width:72px; height:72px; border-radius:50%; border:none; background:var(--accent); color:var(--white); display:flex; align-items:center; justify-content:center; cursor:pointer; box-shadow:var(--shadow-deep); transition:background-color var(--motion-fast) var(--motion-ease), transform var(--motion-fast) var(--motion-ease); }
.live__stop:hover { background:var(--accent-hover); }
.live__stop:active { transform:scale(0.96); }
.live__stop:disabled { opacity:.4; cursor:not-allowed; transform:none; background:var(--accent); }

/* Save panel: raised night surface; shared field/button restyled to stay legible. */
.live__save { display:flex; flex-direction:column; gap:16px; padding:20px 20px calc(20px + env(safe-area-inset-bottom)); background:var(--night-raised); border-top:1px solid color-mix(in srgb, var(--on-night) 10%, transparent); }
.live__save > * { width:100%; max-width:72ch; margin:0 auto; }
.live__save :deep(.h-field__label) { color:var(--on-night-mute); }
.live__save :deep(.h-btn--secondary) { color:var(--on-night); border-color:color-mix(in srgb, var(--on-night) 32%, transparent); }
.live__save :deep(.h-btn--secondary:hover) { background:color-mix(in srgb, var(--on-night) 8%, transparent); }
.live__pending { color:var(--on-night-mute); font-size:var(--fs-sm); margin:0; }
.live__error { color:var(--tomato-soft); font-size:var(--fs-sm); margin:0; }
.live__save-actions { display:flex; gap:10px; justify-content:flex-end; }
</style>
