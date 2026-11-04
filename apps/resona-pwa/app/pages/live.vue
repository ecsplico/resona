<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import type { NewRecording } from "~/data/types";

const cfg = useRuntimeConfig().public;

// When launched from a calendar event (/live?title=…), pre-fill the recording
// name with the event's "<name> – <date>" title.
const route = useRoute();
const presetTitle = typeof route.query.title === "string" ? route.query.title : "";

const live = useLive();
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

const rollingText = computed(() => {
	const f = live.final.value;
	const p = live.partial.value;
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

// Show the reconnect banner only mid-session (after start, before a deliberate
// stop) when the socket drops.
const showReconnect = computed(() => sessionActive.value && live.status.value === "CLOSED");

async function startLive(): Promise<void> {
	if (sessionActive.value) {
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
			await live.start({ stream, language });
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
	await live.start({ language });
}

async function stopLive(): Promise<void> {
	sessionActive.value = false;
	capturedDuration.value = recorder.elapsed.value;
	capturedBlob.value = await recorder.stop();
	// Snapshot AFTER the recorder.stop() await: yielding to the microtask queue
	// lets Vue flush any pending reactive updates from WebSocket messages (e.g.
	// the last partial that arrived just before the user pressed stop).
	// Snapshot BEFORE live.stop(), which removes the data watcher immediately.
	capturedText.value = live.fullText.value.trim();
	live.stop();
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

		await store.createTranscript(recording.id, { text: capturedText.value });

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

onMounted(() => {
	void startLive();
});
</script>

<template>
	<div class="live">
		<HAppBar
			tone="dark"
			title="Live"
		/>

		<div
			v-if="showReconnect"
			class="live__reconnect"
			data-testid="reconnect-banner"
		>
			Verbindung unterbrochen – Wiederverbindung…
		</div>

		<div class="live__body">
			<div
				v-if="rollingText"
				class="live__toolbar"
			>
				<HIconButton
					tone="glass"
					aria-label="Kopieren"
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
				<span class="live__final">{{ live.final.value }}</span>
				<span
					v-if="live.partial.value"
					class="live__partial"
				>{{ live.final.value ? " " : "" }}{{ live.partial.value }}</span>
				<span
					v-if="!rollingText"
					class="live__hint"
				>Sprich jetzt…</span>
			</p>
		</div>

		<div
			v-if="!showTitlePrompt"
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
.live { min-height:100vh; display:flex; flex-direction:column; background:var(--crust-deep, #1a1410); color:var(--white, #fff); }
.live__reconnect { background:var(--tomato-deep, #7a2718); color:var(--white, #fff); padding:10px 20px; font-family:var(--font-body); font-size:14px; font-weight:600; text-align:center; }
.live__body { flex:1; padding:24px 20px; overflow-y:auto; }
.live__toolbar { display:flex; justify-content:flex-end; margin-bottom:12px; }
.live__transcript { white-space:pre-wrap; font-family:var(--font-body); font-size:20px; line-height:1.6; margin:0; }
.live__final { color:var(--white, #fff); }
.live__partial { color:var(--fg3, #9a8f86); }
.live__hint { color:var(--fg3, #9a8f86); font-style:italic; }
.live__controls { display:flex; justify-content:center; padding:28px 0 40px; }
.live__stop { width:72px; height:72px; border-radius:50%; border:none; background:var(--accent, #e0563a); color:var(--white, #fff); display:flex; align-items:center; justify-content:center; cursor:pointer; box-shadow:var(--shadow-lift); }
.live__save { display:flex; flex-direction:column; gap:14px; padding:20px; background:var(--crust, #241c16); }
.live__error { color:var(--danger); font-size:14px; margin:0; }
.live__save-actions { display:flex; gap:10px; justify-content:flex-end; }
</style>
