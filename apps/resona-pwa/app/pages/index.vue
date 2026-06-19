<script setup lang="ts">
import { onMounted, ref } from "vue";
import type { Recording } from "~/types/directus";

const { items, pending, error, list, refresh } = useRecordings();
const recorder = useRecorder();
const { saveRecording } = useUpload();

const search = ref("");
const since = ref("");

// Record flow state: FAB -> HRecordingSheet (recording) -> SaveSheet (review).
const showRecorder = ref(false);
const showSave = ref(false);
const pendingBlob = ref<Blob | null>(null);
const pendingDuration = ref(0);
const pendingTitle = ref("");
const uploadInput = ref<HTMLInputElement | null>(null);

function applyFilters(): void {
	void list({ search: search.value, since: since.value });
}

function openRecording(recording: Recording): void {
	void navigateTo(`/recording/${recording.id}`);
}

function copyRecording(recording: Recording): void {
	// STUB: real transcript copy arrives with useExport in Task 20. Copies the
	// title as a placeholder for now.
	void navigator.clipboard?.writeText(recording.title);
}

async function startRecording(): Promise<void> {
	showRecorder.value = true;
	await recorder.start();
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
	await saveRecording({
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
}

function onDiscard(): void {
	pendingBlob.value = null;
	showSave.value = false;
}

onMounted(() => {
	void list();
});
</script>

<template>
	<div class="home">
		<HAppBar
			tone="light"
			title="Resona"
		>
			<template #trailing>
				<NuxtLink
					class="home__live"
					to="/live"
				>
					Live
				</NuxtLink>
			</template>
		</HAppBar>

		<div class="home__filters">
			<HField
				v-model="search"
				class="home__search"
				label="Suchen"
				@update:model-value="applyFilters"
			/>
			<label class="home__since">
				<span class="home__since-label">Ab Datum</span>
				<input
					v-model="since"
					class="home__since-input"
					type="date"
					@change="applyFilters"
				>
			</label>
		</div>

		<div class="home__list">
			<p
				v-if="error"
				class="home__error"
			>
				{{ error }}
			</p>

			<p
				v-if="pending"
				class="home__hint"
			>
				Lädt…
			</p>

			<p
				v-else-if="items.length === 0"
				class="home__hint"
			>
				Noch keine Aufnahmen
			</p>

			<RecordingListItem
				v-for="recording in items"
				:key="recording.id"
				:recording="recording"
				@open="openRecording(recording)"
				@copy="copyRecording(recording)"
			/>
		</div>

		<button
			class="home__fab"
			type="button"
			data-testid="record-fab"
			aria-label="Aufnahme starten"
			@click="startRecording"
		>
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
		</button>

		<button
			class="home__upload"
			type="button"
			data-testid="upload-trigger"
			aria-label="Audiodatei hochladen"
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
		</button>

		<input
			ref="uploadInput"
			class="home__upload-input"
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
.home { min-height:100vh; background:var(--bg); padding-bottom:120px; }
.home__live { color:var(--accent); font-weight:600; font-size:15px; text-decoration:none; }
.home__filters { display:flex; gap:12px; align-items:flex-end; padding:16px 20px 8px; }
.home__search { flex:1; }
.home__since { display:flex; flex-direction:column; }
.home__since-label { font-size:13px; font-weight:600; color:var(--crust-deep); margin-bottom:7px; }
.home__since-input { border:1.5px solid var(--border-strong); border-radius:8px; padding:12px 14px; background:var(--white); font-family:var(--font-body); font-size:15px; color:var(--fg1); }
.home__list { display:flex; flex-direction:column; gap:12px; padding:8px 20px 20px; }
.home__error { color:var(--danger); font-size:14px; margin:0; }
.home__hint { color:var(--fg3); font-size:15px; margin:0; text-align:center; padding:32px 0; }
.home__fab { position:fixed; left:50%; bottom:28px; transform:translateX(-50%); width:68px; height:68px; border-radius:50%; border:none; background:var(--accent); color:var(--white); display:flex; align-items:center; justify-content:center; cursor:pointer; box-shadow:var(--shadow-lift); }
.home__upload { position:fixed; right:24px; bottom:36px; width:52px; height:52px; border-radius:50%; border:1.5px solid var(--border-strong); background:var(--white); color:var(--fg1); display:flex; align-items:center; justify-content:center; cursor:pointer; box-shadow:var(--shadow-lift); }
.home__upload-input { display:none; }
</style>
