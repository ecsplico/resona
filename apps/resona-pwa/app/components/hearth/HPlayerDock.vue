<script setup lang="ts">
import { computed, ref } from "vue";

// `marks`: seconds where a later part (re-dictation) starts in the audio; each is a
// faint tick on the track. The default slot sits at the right end of the dock.
const props = defineProps<{ src: string; fallbackDuration?: number; marks?: number[] }>();

const audioEl = ref<HTMLAudioElement | null>(null);
const playing = ref(false);
const current = ref(0);
const duration = ref(0);

function formatTime(seconds: number): string {
	if (!Number.isFinite(seconds) || seconds < 0) {
		return "0:00";
	}
	const total = Math.floor(seconds);
	const m = Math.floor(total / 60);
	const s = total % 60;
	return `${m}:${s.toString().padStart(2, "0")}`;
}

function toggle(): void {
	const el = audioEl.value;
	if (!el) {
		// No element (test env) — still flip the flag so the UI reflects intent.
		playing.value = !playing.value;
		return;
	}
	if (playing.value) {
		try {
			el.pause?.();
		}
		catch {
			// ignore — media APIs may be absent in happy-dom
		}
		playing.value = false;
	}
	else {
		try {
			const p = el.play?.();
			if (p && typeof p.catch === "function") {
				p.catch(() => {});
			}
		}
		catch {
			// ignore — media APIs may be absent in happy-dom
		}
		playing.value = true;
	}
}

function onTimeUpdate(): void {
	if (audioEl.value) {
		current.value = audioEl.value.currentTime;
	}
}

function onLoadedMetadata(): void {
	const d = audioEl.value?.duration;
	// MediaRecorder webm files often have no duration in the container header,
	// so the audio element reports Infinity. Fall back to the stored value.
	if (Number.isFinite(d) && d! > 0) {
		duration.value = d!;
	}
	else if (props.fallbackDuration && props.fallbackDuration > 0) {
		duration.value = props.fallbackDuration;
	}
}

function onEnded(): void {
	playing.value = false;
	current.value = 0;
}

function seek(event: MouseEvent): void {
	const target = event.currentTarget as HTMLElement | null;
	if (!target || duration.value <= 0) {
		return;
	}
	const rect = target.getBoundingClientRect();
	const width = rect.width || 1;
	const fraction = Math.min(1, Math.max(0, (event.clientX - rect.left) / width));
	const next = fraction * duration.value;
	current.value = next;
	if (audioEl.value) {
		audioEl.value.currentTime = next;
	}
}

// Ticks inside the track only: a mark at 0 or past the end separates nothing.
const markPercents = computed(() => {
	const total = duration.value > 0 ? duration.value : props.fallbackDuration ?? 0;
	if (total <= 0) return [];
	return (props.marks ?? []).filter(s => s > 0 && s < total).map(s => (s / total) * 100);
});

defineExpose({ formatTime });
</script>

<template>
	<div class="h-dock">
		<audio
			ref="audioEl"
			:src="src"
			class="h-dock__audio"
			preload="metadata"
			@timeupdate="onTimeUpdate"
			@loadedmetadata="onLoadedMetadata"
			@ended="onEnded"
		/>

		<button
			type="button"
			class="h-dock__play"
			:class="{ 'is-playing': playing }"
			data-testid="player-play"
			:aria-label="playing ? 'Pause' : 'Abspielen'"
			@click="toggle"
		>
			<svg
				v-if="playing"
				width="20"
				height="20"
				viewBox="0 0 24 24"
				fill="currentColor"
				aria-hidden="true"
			>
				<rect
					x="6"
					y="5"
					width="4"
					height="14"
					rx="1"
				/>
				<rect
					x="14"
					y="5"
					width="4"
					height="14"
					rx="1"
				/>
			</svg>
			<svg
				v-else
				width="20"
				height="20"
				viewBox="0 0 24 24"
				fill="currentColor"
				aria-hidden="true"
			>
				<path d="M8 5v14l11-7z" />
			</svg>
		</button>

		<span class="h-dock__time">{{ formatTime(current) }}</span>
		<div
			class="h-dock__track"
			data-testid="player-track"
			@click="seek"
		>
			<div
				class="h-dock__fill"
				:style="{ width: (duration > 0 ? (current / duration) * 100 : 0) + '%' }"
			/>
			<span
				v-for="at in markPercents"
				:key="at"
				class="h-dock__mark"
				data-testid="player-mark"
				:style="{ left: `${at}%` }"
			/>
			<span
				class="h-dock__thumb"
				:style="{ left: (duration > 0 ? (current / duration) * 100 : 0) + '%' }"
			/>
		</div>
		<span class="h-dock__time">{{ formatTime(duration) }}</span>
		<slot />
	</div>
</template>

<style scoped>
.h-dock { background:var(--white); border-top:1px solid var(--border); padding:16px 20px 24px; display:flex; align-items:center; gap:12px; }
.h-dock__audio { display:none; }
.h-dock__play { width:52px; height:52px; flex:0 0 auto; border:none; border-radius:50%; background:var(--olive); color:var(--white); display:inline-flex; align-items:center; justify-content:center; cursor:pointer; box-shadow:var(--shadow-lift); transition:background var(--motion-fast) var(--motion-ease); }
.h-dock__play:hover { background:var(--olive-deep); }
/* The track: a 6px bar in a taller hit area, centred on the dock's row. */
.h-dock__track { position:relative; flex:1; min-width:60px; height:24px; cursor:pointer; }
.h-dock__track::before { content:""; position:absolute; left:0; right:0; top:50%; height:6px; transform:translateY(-50%); border-radius:999px; background:var(--crust-soft); }
.h-dock__fill { position:absolute; left:0; top:50%; height:6px; transform:translateY(-50%); border-radius:999px; background:var(--olive); }
/* Where a later part starts: a light gray tick across the bar, barely visible. */
.h-dock__mark { position:absolute; top:50%; width:2px; height:14px; transform:translate(-50%,-50%); border-radius:1px; background:rgba(0,0,0,0.13); pointer-events:none; }
.h-dock__thumb { position:absolute; top:50%; transform:translate(-50%,-50%); width:16px; height:16px; border-radius:50%; background:var(--white); border:2px solid var(--olive); box-shadow:var(--shadow-soft); pointer-events:none; }
.h-dock__time { flex:0 0 auto; font-family:var(--font-mono); font-size:12px; color:var(--fg3); font-variant-numeric:tabular-nums; }
</style>
