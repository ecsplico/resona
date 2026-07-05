<script setup lang="ts">
import { ref } from "vue";

const props = defineProps<{ src: string; fallbackDuration?: number }>();

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

		<div class="h-dock__body">
			<div
				class="h-dock__track"
				data-testid="player-track"
				@click="seek"
			>
				<div
					class="h-dock__fill"
					:style="{ width: (duration > 0 ? (current / duration) * 100 : 0) + '%' }"
				>
					<span class="h-dock__thumb" />
				</div>
			</div>
			<div class="h-dock__time">
				<span>{{ formatTime(current) }}</span>
				<span>{{ formatTime(duration) }}</span>
			</div>
		</div>
	</div>
</template>

<style scoped>
.h-dock { background:var(--white); border-top:1px solid var(--border); padding:16px 20px 24px; display:flex; align-items:center; gap:14px; }
.h-dock__audio { display:none; }
.h-dock__play { width:52px; height:52px; flex:0 0 auto; border:none; border-radius:50%; background:var(--accent); color:var(--white); display:inline-flex; align-items:center; justify-content:center; cursor:pointer; box-shadow:var(--shadow-lift); }
.h-dock__body { flex:1; display:flex; flex-direction:column; gap:8px; }
.h-dock__track { position:relative; height:6px; border-radius:999px; background:var(--crust-soft); cursor:pointer; }
.h-dock__fill { position:relative; height:100%; border-radius:999px; background:var(--accent); }
.h-dock__thumb { position:absolute; right:0; top:50%; transform:translate(50%,-50%); width:16px; height:16px; border-radius:50%; background:var(--white); border:2px solid var(--accent); box-shadow:var(--shadow-soft); }
.h-dock__time { display:flex; justify-content:space-between; font-family:var(--font-mono); font-size:12px; color:var(--fg3); }
</style>
