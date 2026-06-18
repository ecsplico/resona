<script setup lang="ts">
import { onMounted, ref } from "vue";
import type { Recording } from "~/types/directus";

const { items, pending, error, list } = useRecordings();

const search = ref("");
const since = ref("");

// STUB: the full record flow (recorder sheet) is built in Task 14/15. For now
// the FAB only flips this flag; nothing is mounted for it yet.
const showRecorder = ref(false);

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

function startRecording(): void {
	// STUB: Task 14/15 will mount the recorder sheet when this is true.
	showRecorder.value = true;
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
</style>
