<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { NewRecording } from "~/data/types";
import { HubError, type ListenEngine } from "~/composables/useHub";
import { DEVICE_SPEECH_HINTS, type DeviceSpeechAvailability } from "~/composables/useDeviceSpeech";
import { describeClose } from "~/composables/useLive";
import { LIVE_SUGGESTION_MIN_WORDS, meaningfulWordCount, suggestTitle, TITLE_REFINE_MIN_WORDS } from "~/utils/titleSuggestion";
import { refineTitle as refineTitleOnDevice } from "~/utils/titleRefinement";
import { discardUnfinished } from "~/data/unfinishedRecordings";

const cfg = useRuntimeConfig().public;
const nav = useBackNav();

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
const selectedListenEngine = computed(() => {
	const name = settings.hubEngine || listenDefault.value;
	return listenEngines.value.find(e => e.name === name);
});

// Why the hub source cannot stream, and whether it blocks start: the blocking ones
// need a settings change; "unreachable"/"error" are re-checked when the user presses start.
type HubIssue = "no-token" | "unauthorized" | "no-engine" | "unreachable" | "error";
const HUB_ISSUES: Record<HubIssue, { blocking: boolean; text: (detail?: string) => string }> = {
	"no-token": { blocking: true, text: () => "Auf diesem Gerät ist kein Hub-Token hinterlegt – in Einstellungen → Verbindung eintragen." },
	"unauthorized": { blocking: true, text: () => "Der Hub lehnt das Token dieses Geräts ab (ungültig oder widerrufen) – in Einstellungen → Verbindung ein neues eintragen." },
	"no-engine": { blocking: true, text: () => "Der Hub hat keinen Live-Erkenner – in Einstellungen → Live die lokale Erkennung wählen." },
	"unreachable": { blocking: false, text: () => "Der Hub ist nicht erreichbar – Netzwerkverbindung prüfen und erneut starten." },
	"error": { blocking: false, text: detail => `Die Live-Erkenner des Hubs konnten nicht geladen werden (${detail}).` },
};
const hubIssue = ref<{ kind: HubIssue; detail?: string } | null>(null);
const deviceAvailability = ref<DeviceSpeechAvailability | null>(null);
// Both checks have run; until then start stays disabled.
const checked = ref(false);

const deviceUnsupported = computed(() =>
	settings.source === "device"
	&& (deviceAvailability.value === "unsupported" || deviceAvailability.value === "unavailable"),
);
const startBlocked = computed(() => {
	if (settings.source === "device") {
		return deviceUnsupported.value;
	}
	const issue = hubIssue.value;
	return issue !== null && HUB_ISSUES[issue.kind].blocking;
});
const startNotice = computed<{ kind: string; text: string } | null>(() => {
	if (settings.source === "device") {
		const state = deviceAvailability.value;
		if (state === "unsupported" || state === "unavailable") {
			return { kind: "device-unsupported", text: `${DEVICE_SPEECH_HINTS[state]} – in Einstellungen → Live den Hub als Quelle wählen.` };
		}
		if (state === "downloadable" || state === "downloading") {
			return { kind: `device-${state}`, text: `${DEVICE_SPEECH_HINTS[state]}.` };
		}
		return null;
	}
	const issue = hubIssue.value;
	return issue ? { kind: issue.kind, text: HUB_ISSUES[issue.kind].text(issue.detail) } : null;
});

// The microphone stream shared by capture (transcription) and recorder (audio).
let liveStream: MediaStream | null = null;

function releaseLiveStream(): void {
	for (const track of liveStream?.getTracks() ?? []) track.stop();
	liveStream = null;
}

/** After an interruption: a fresh microphone for the recorder's next part and the transcription. */
async function reopenLiveStream(): Promise<MediaStream> {
	let next: MediaStream;
	try {
		next = await navigator.mediaDevices.getUserMedia({ audio: true });
	}
	catch (err) {
		throw new Error("Mikrofon konnte nicht gestartet werden.", { cause: err });
	}
	if (!sessionActive.value) {
		// Stopped while the microphone was opening: transcription stays off.
		for (const track of next.getTracks()) track.stop();
		throw new Error("Live-Diktat wurde beendet.");
	}
	releaseLiveStream();
	liveStream = next;
	capture.stop();
	await capture.start(next);
	return next;
}

// The live audio also goes to IndexedDB as it is recorded, so a killed page offers it again.
// Live records the stream it already streams to the hub, so it stays on the web
// recorder: live dictation runs in the web view and is foreground-only, in the app too.
const recorder = useWebRecorder({
	persist: () => ({ kind: "live", label: presetTitle || "Live-Diktat" }),
	reopen: reopenLiveStream,
});
// Also without a recorder (no supported container) the screen stays on while live runs.
const sessionWake = useWakeLock();

// Interrupted (microphone lost, app in the background): transcription pauses with the
// audio, so the text never holds what the recording misses. "Fortsetzen" reopens both.
watch(() => recorder.interrupted.value, (reason) => {
	if (!reason || !sessionActive.value) return;
	capture.stop();
	releaseLiveStream();
});

// Persisted recorder session of the captured audio, deleted once it is saved or thrown away.
let capturedSession: string | null = null;

function dropCapturedSession(): void {
	const session = capturedSession;
	capturedSession = null;
	if (session) void discardUnfinished(session);
}
const store = useDataStore();
const { copy } = useExport();

const sessionActive = ref(false);
const showTitlePrompt = ref(false);
const saving = ref(false);
const saveError = ref<string | null>(null);
const title = ref(presetTitle);
// Set once the user types in the title field; suggestions never replace their input.
const titleEdited = ref(false);
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

// Title suggestion from the final text (issue #4), shown once enough words are in so
// it does not flip with every early word. A calendar preset wins, so no competing
// suggestion is shown or applied then.
const ruleTitleSuggestion = computed(() => {
	if (presetTitle.trim()) {
		return "";
	}
	const text = correctionActive.value ? correction.text.value : source.value.final.value;
	return meaningfulWordCount(text) >= LIVE_SUGGESTION_MIN_WORDS ? suggestTitle(text) : "";
});
// Frozen on stop: a correction finishing later never changes the save field; only the
// model title below may still replace it.
const stoppedTitleSuggestion = ref("");
const liveTitleSuggestion = computed(() => (sessionActive.value ? ruleTitleSuggestion.value : stoppedTitleSuggestion.value));
// The browser's own language model (Prompt API) refines first: on the device, no
// network, and only when it is ready right now (nothing is downloaded). Once per
// recording: when the final text reaches DEVICE_REFINE_MIN_WORDS, or after stop if
// it never did. Only awaits a promise, so capture and the live transcript keep running.
const DEVICE_REFINE_MIN_WORDS = 40;
const deviceTitle = ref<string | null>(null);
let deviceRun: Promise<string | null> | null = null;
let deviceAbort: AbortController | null = null;
// After stop a small hub model may refine the rule suggestion (title-de text job),
// only when the on-device model gave no title.
const titleJob = useTitleJob();
const remoteTitle = ref<string | null>(null);
const titleRefining = ref(false);
let titleAbort: AbortController | null = null;
const shownSuggestion = computed(() => remoteTitle.value || deviceTitle.value || liveTitleSuggestion.value);
watch(shownSuggestion, (suggestion) => {
	if (suggestion && !titleEdited.value) {
		title.value = suggestion;
	}
});

// Null without a usable title (no ready model, abort, unusable answer).
function startDeviceTitle(text: string): Promise<string | null> {
	if (!deviceRun) {
		const ctrl = new AbortController();
		deviceAbort = ctrl;
		deviceRun = refineTitleOnDevice(text, { language: cfg.defaultLanguage, signal: ctrl.signal }).then((refined) => {
			if (!refined || ctrl.signal.aborted || titleEdited.value) {
				return null;
			}
			deviceTitle.value = refined;
			return refined;
		});
	}
	return deviceRun;
}

const finalTextForTitle = (): string => (correctionActive.value ? correction.text.value : source.value.final.value);
watch(
	() => sessionActive.value && !presetTitle.trim() && meaningfulWordCount(finalTextForTitle()) >= DEVICE_REFINE_MIN_WORDS,
	(ready) => {
		if (ready) {
			void startDeviceTitle(finalTextForTitle());
		}
	},
);

// The save dialog gets exactly the suggestion shown during recording; without one
// (too little text) the rule title of the captured text, else the preset or
// "Diktat <Datum Uhrzeit>".
function applySuggestedTitle(): void {
	if (!titleEdited.value) {
		title.value = shownSuggestion.value || suggestTitle(capturedText.value, { calendarTitle: presetTitle });
	}
}

function cancelTitleRefinement(): void {
	deviceAbort?.abort();
	deviceAbort = null;
	titleAbort?.abort();
	titleAbort = null;
	titleRefining.value = false;
}

// Best effort: null, an abort or a failed correction keep the rule suggestion silently.
async function refineTitle(): Promise<void> {
	titleAbort?.abort();
	titleRefining.value = false;
	if (presetTitle.trim() || titleEdited.value) {
		return;
	}
	const ctrl = new AbortController();
	titleAbort = ctrl;
	const hubAllowed = useHub().hasToken();
	titleRefining.value = hubAllowed;
	try {
		if (correctionDone) {
			await correctionDone;
		}
		const text = capturedText.value;
		if (ctrl.signal.aborted || meaningfulWordCount(text) < TITLE_REFINE_MIN_WORDS) {
			return;
		}
		if (await startDeviceTitle(text) || ctrl.signal.aborted || !hubAllowed) {
			return;
		}
		const remote = await titleJob.suggestTitleRemote(text, { language: cfg.defaultLanguage, signal: ctrl.signal });
		if (remote && !ctrl.signal.aborted && !titleEdited.value) {
			remoteTitle.value = remote;
		}
	}
	catch {
		// correction failed: confirmSave reports it; keep the rule title
	}
	finally {
		if (titleAbort === ctrl) {
			titleAbort = null;
			titleRefining.value = false;
		}
	}
}

function onTitleInput(value: string): void {
	title.value = value;
	titleEdited.value = true;
	cancelTitleRefinement();
}

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
const reconnectText = computed(() => {
	const info = settings.source === "hub" ? hubLive.closeInfo.value : null;
	return info
		? `Verbindung unterbrochen: ${describeClose(info)} – Wiederverbindung…`
		: "Verbindung unterbrochen – Wiederverbindung…";
});

async function loadListenEngines(): Promise<void> {
	listenEngines.value = [];
	listenDefault.value = null;
	hubIssue.value = null;
	const hub = useHub();
	if (!hub.hasToken()) {
		hubIssue.value = { kind: "no-token" };
		return;
	}
	try {
		const res = await hub.listListenEngines();
		listenEngines.value = res.engines;
		listenDefault.value = res.default;
		if (res.engines.length === 0) {
			hubIssue.value = { kind: "no-engine" };
		}
	}
	catch (err) {
		if (!(err instanceof HubError) || err.status === 0 || err.status === 502 || err.status === 503 || err.status === 504) {
			// fetch failure, or a proxy in front of a hub that is down
			hubIssue.value = { kind: "unreachable" };
		}
		else if (err.status === 401 || err.status === 403) {
			hubIssue.value = { kind: "unauthorized" };
		}
		else {
			hubIssue.value = { kind: "error", detail: `HTTP ${err.status}${err.message ? `: ${err.message}` : ""}` };
		}
	}
}

async function checkDevice(): Promise<void> {
	try {
		deviceAvailability.value = await device.availability(cfg.defaultLanguage);
	}
	catch {
		deviceAvailability.value = "unavailable";
	}
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
	if (sessionActive.value || startBlocked.value) {
		return;
	}
	if (settings.source === "hub" && hubIssue.value) {
		// transient issue (unreachable / server error): check again before starting
		await loadListenEngines();
		if (hubIssue.value || sessionActive.value) {
			return;
		}
	}
	sessionActive.value = true;
	sessionWake.acquire();
	cancelTitleRefinement();
	deviceRun = null;
	deviceTitle.value = null;
	remoteTitle.value = null;

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
			liveStream = stream;
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
	stoppedTitleSuggestion.value = ruleTitleSuggestion.value;
	sessionActive.value = false;
	capturedDuration.value = recorder.elapsed.value;
	capturedSession = recorder.sessionId.value;
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
	releaseLiveStream();
	sessionWake.release();
	applySuggestedTitle();
	showTitlePrompt.value = true;
	void refineTitle();
}

async function confirmSave(): Promise<void> {
	if (saving.value) {
		return;
	}
	saving.value = true;
	saveError.value = null;
	// Save what the dialog shows: a correction finishing during the save must not swap the title.
	titleEdited.value = true;
	cancelTitleRefinement();
	try {
		if (correctionDone) {
			await correctionDone;
		}
		const blob = capturedBlob.value && capturedBlob.value.size > 0 ? capturedBlob.value : null;
		// A cleared field saves under the rule suggestion (else "Diktat <Datum Uhrzeit>").
		const recordingTitle = title.value.trim() || suggestTitle(capturedText.value, { calendarTitle: presetTitle });
		const input: NewRecording = {
			title: recordingTitle,
			language: cfg.defaultLanguage,
			profile: cfg.defaultProfile,
			source: "live",
			status: "done",
			duration_seconds: capturedDuration.value,
		};
		const recording = await store.createRecording(input, blob, `${recordingTitle}.webm`);
		// The audio is stored with the recording now.
		dropCapturedSession();

		await store.createTranscript(recording.id, { text: capturedText.value, engine: capturedEngine.value });

		void nav.leaveTo("/");
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
	cancelTitleRefinement();
	showTitlePrompt.value = false;
	dropCapturedSession();
	void nav.leaveTo("/");
}

onBeforeUnmount(() => {
	cancelTitleRefinement();
	releaseLiveStream();
});

onMounted(async () => {
	await Promise.all([
		loadListenEngines(),
		settings.correction ? loadCorrectionEngines() : Promise.resolve(),
		settings.source === "device" ? checkDevice() : Promise.resolve(),
	]);
	checked.value = true;
	// A device pack that is not installed yet would only fail; wait for the user.
	if (settings.source === "device" && deviceAvailability.value !== "available") {
		return;
	}
	await startLive();
});
</script>

<template>
	<div class="live">
		<header class="live__bar">
			<button
				type="button"
				class="live__back"
				data-testid="live-back"
				@click="nav.back('/')"
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
			</button>
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
			{{ reconnectText }}
		</div>

		<div class="live__body">
			<div class="live__column">
				<p
					v-if="(sessionActive || showTitlePrompt) && shownSuggestion"
					class="live__suggestion"
					data-testid="live-title-suggestion"
				>
					Titelvorschlag: {{ shownSuggestion }}
				</p>
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
			v-if="startNotice"
			class="live__notice"
			data-testid="live-notice"
			:data-kind="startNotice.kind"
			role="status"
		>
			{{ startNotice.text }}
		</p>

		<div
			v-if="sessionActive && recorder.interruption.value"
			class="live__interrupted"
			role="alert"
			data-testid="live-interrupted"
		>
			<p class="live__interrupted-text">
				<strong>Aufnahme unterbrochen</strong>
				{{ recorder.interruption.value }}
			</p>
			<HButton
				variant="primary"
				data-testid="live-continue"
				@click="recorder.continueRecording()"
			>
				Fortsetzen
			</HButton>
		</div>

		<div
			v-if="!showTitlePrompt && !sessionActive"
			class="live__controls"
		>
			<button
				class="live__stop"
				type="button"
				data-testid="start-live"
				aria-label="Aufnahme starten"
				:disabled="startBlocked || !checked"
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
				:model-value="title"
				class="live__title"
				label="Titel"
				data-testid="title-input"
				@update:model-value="onTitleInput"
			/>
			<p
				v-if="titleRefining"
				class="live__pending"
				data-testid="live-title-refining"
			>
				Titel wird verfeinert…
			</p>
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
.live__back { justify-self:start; display:inline-flex; align-items:center; gap:4px; min-height:40px; padding:0 12px 0 6px; border-radius:var(--r-full); color:var(--on-night); font-size:var(--fs-sm); font-weight:600; text-decoration:none; border:0; background:none; cursor:pointer; font-family:inherit; transition:background-color var(--motion-fast) var(--motion-ease); }
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
.live__suggestion { margin:0 0 12px; color:var(--on-night-mute); font-size:var(--fs-sm); line-height:1.4; }

.live__notice { width:calc(100% - 40px); max-width:72ch; margin:0 auto 12px; padding:12px 14px; border:1px solid color-mix(in srgb, var(--warning) 45%, transparent); border-radius:var(--r-md); background:var(--night-raised); color:var(--on-night); font-size:var(--fs-sm); line-height:1.5; }
.live__interrupted { width:calc(100% - 40px); max-width:72ch; margin:0 auto 12px; padding:12px 14px; display:flex; flex-wrap:wrap; align-items:center; gap:10px 14px; border:1px solid color-mix(in srgb, var(--warning) 45%, transparent); border-radius:var(--r-md); background:var(--night-raised); color:var(--on-night); font-size:var(--fs-sm); line-height:1.5; }
.live__interrupted-text { flex:1 1 220px; margin:0; }
.live__interrupted-text strong { display:block; }

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
