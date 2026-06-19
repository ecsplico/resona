<script setup lang="ts">
import { createItem } from "@directus/sdk";
import { computed, onMounted, ref } from "vue";
import type { Recording, Transcript } from "~/types/directus";

const cfg = useRuntimeConfig().public;

const live = useLive();
const recorder = useRecorder();
const { uploadBlob } = useUpload();
const { client } = useDirectus();

const sessionActive = ref(false);
const showTitlePrompt = ref(false);
const saving = ref(false);
const title = ref("");
const capturedBlob = ref<Blob | null>(null);
const capturedDuration = ref(0);

const rollingText = computed(() => {
	const f = live.final.value;
	const p = live.partial.value;
	if (f && p) {
		return `${f} ${p}`;
	}
	return f || p;
});

// Show the reconnect banner only mid-session (after start, before a deliberate
// stop) when the socket drops.
const showReconnect = computed(() => sessionActive.value && live.status.value === "CLOSED");

async function startLive(): Promise<void> {
	if (sessionActive.value) {
		return;
	}
	sessionActive.value = true;

	const language = cfg.defaultLanguage;
	const engine = cfg.defaultEngine;

	// Acquire the mic ONCE and share the single stream with both the live
	// transcriber and the parallel recorder so we don't open the mic twice.
	const media = (globalThis as { navigator?: Navigator }).navigator?.mediaDevices;
	if (media?.getUserMedia) {
		try {
			const stream = await media.getUserMedia({ audio: true });
			await live.start({ stream, language, engine });
			await recorder.start(stream);
			return;
		}
		catch {
			// fall through to transcript-only mode
		}
	}

	// No mic (tests / SSR): still start the transcript path; useLive guards audio.
	await live.start({ language, engine });
}

async function stopLive(): Promise<void> {
	sessionActive.value = false;
	capturedDuration.value = recorder.elapsed.value;
	capturedBlob.value = await recorder.stop();
	live.stop();
	title.value = "";
	showTitlePrompt.value = true;
}

async function confirmSave(): Promise<void> {
	if (saving.value) {
		return;
	}
	saving.value = true;
	try {
		let fileId: string | null = null;
		if (capturedBlob.value && capturedBlob.value.size > 0) {
			fileId = await uploadBlob(capturedBlob.value, title.value);
		}

		const recording = await client.request(createItem("recordings", {
			title: title.value,
			audio_file: fileId ?? undefined,
			language: cfg.defaultLanguage,
			profile: cfg.defaultProfile,
			source: "live",
			status: "done",
			duration_seconds: capturedDuration.value,
		})) as Recording;

		await client.request(createItem("transcripts", {
			recording: recording.id,
			text: live.final.value,
			engine: cfg.defaultEngine,
		} as Partial<Transcript>));

		void navigateTo("/");
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
.live__transcript { white-space:pre-wrap; font-family:var(--font-body); font-size:20px; line-height:1.6; margin:0; }
.live__final { color:var(--white, #fff); }
.live__partial { color:var(--fg3, #9a8f86); }
.live__hint { color:var(--fg3, #9a8f86); font-style:italic; }
.live__controls { display:flex; justify-content:center; padding:28px 0 40px; }
.live__stop { width:72px; height:72px; border-radius:50%; border:none; background:var(--accent, #e0563a); color:var(--white, #fff); display:flex; align-items:center; justify-content:center; cursor:pointer; box-shadow:var(--shadow-lift); }
.live__save { display:flex; flex-direction:column; gap:14px; padding:20px; background:var(--crust, #241c16); }
.live__save-actions { display:flex; gap:10px; justify-content:flex-end; }
</style>
