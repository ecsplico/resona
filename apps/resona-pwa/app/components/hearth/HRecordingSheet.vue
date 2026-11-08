<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

// `label` replaces the default heading, e.g. "Weiter aufnehmen" when continuing a recording.
const props = defineProps<{ elapsed: number; levels: number[]; paused?: boolean; label?: string }>();

// Only the newest bars that fit the sheet (7 px each incl. gap, 432 px dialog / phone width).
const MAX_BARS = 44;
// Speech RMS sits around 0.05-0.3, so bars are scaled up to use the wave's height.
const LEVEL_GAIN = 3;

const bars = computed(() => props.levels.slice(-MAX_BARS).map(level => Math.max(4, Math.round(Math.min(1, level * LEVEL_GAIN) * 100))));

const emit = defineEmits<{ pause: []; resume: []; stop: []; cancel: [] }>();

// Wide screens: a centered dialog instead of a bottom sheet.
const { wide } = useShell();
const stopButton = ref<HTMLButtonElement | null>(null);

onMounted(() => {
	// Keyboard users land on the primary control; phones keep their focus as is.
	if (wide.value) {
		stopButton.value?.focus();
	}
});

function formatTime(seconds: number): string {
	if (!Number.isFinite(seconds) || seconds < 0) {
		return "00:00";
	}
	const total = Math.floor(seconds);
	const m = Math.floor(total / 60);
	const s = total % 60;
	return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

function togglePause(): void {
	if (props.paused) {
		emit("resume");
	}
	else {
		emit("pause");
	}
}
</script>

<template>
	<div
		class="h-rec"
		:class="{ 'h-rec--dialog': wide }"
		role="dialog"
		aria-modal="true"
		aria-labelledby="h-rec-title"
		data-testid="recording-sheet"
	>
		<div class="h-rec__scrim" />
		<div class="h-rec__sheet">
			<p
				id="h-rec-title"
				class="h-rec__eyebrow"
			>
				{{ label ?? "AUFNAHME LÄUFT" }}
			</p>

			<div
				class="h-rec__wave"
				:class="{ 'is-paused': paused }"
			>
				<span
					v-for="(height, i) in bars"
					:key="i"
					class="h-rec__bar"
					data-testid="wave-bar"
					:style="{ height: height + '%' }"
				/>
			</div>

			<div class="h-rec__timer">
				<span class="h-rec__pulse" />
				<span
					class="h-rec__time"
					data-testid="timer"
				>{{ formatTime(elapsed) }}</span>
			</div>

			<div class="h-rec__controls">
				<button
					type="button"
					class="h-rec__btn h-rec__btn--cancel"
					data-testid="cancel"
					aria-label="Verwerfen"
					@click="emit('cancel')"
				>
					<svg
						width="20"
						height="20"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						aria-hidden="true"
					>
						<path d="M6 6l12 12M18 6L6 18" />
					</svg>
				</button>

				<button
					ref="stopButton"
					type="button"
					class="h-rec__btn h-rec__btn--stop"
					data-testid="stop"
					aria-label="Aufnahme beenden"
					@click="emit('stop')"
				>
					<span class="h-rec__square" />
				</button>

				<button
					type="button"
					class="h-rec__btn h-rec__btn--pause"
					data-testid="pause"
					:aria-label="paused ? 'Fortsetzen' : 'Pausieren'"
					@click="togglePause"
				>
					<svg
						v-if="paused"
						width="20"
						height="20"
						viewBox="0 0 24 24"
						fill="currentColor"
						aria-hidden="true"
					>
						<path d="M8 5v14l11-7z" />
					</svg>
					<svg
						v-else
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
				</button>
			</div>
		</div>
	</div>
</template>

<style scoped>
.h-rec { position:fixed; inset:0; z-index:50; display:flex; flex-direction:column; justify-content:flex-end; }
.h-rec__scrim { position:absolute; inset:0; background:rgba(35,25,17,0.72); }
.h-rec__sheet { position:relative; background:var(--night); color:var(--white); border-radius:var(--r-lg) var(--r-lg) 0 0; padding:28px 24px 36px; display:flex; flex-direction:column; align-items:center; gap:24px; }
/* Wide: centered dialog, max 480 px, rises in on open. */
.h-rec--dialog { justify-content:center; align-items:center; padding:24px; }
.h-rec--dialog .h-rec__sheet { width:100%; max-width:480px; border-radius:var(--r-lg); box-shadow:var(--shadow-deep); animation:h-rec-in var(--motion-base) var(--motion-ease); }
@keyframes h-rec-in { from { opacity:0; transform:translateY(8px); } }
@media (prefers-reduced-motion:reduce) { .h-rec--dialog .h-rec__sheet { animation:none; } }
.h-rec__eyebrow { margin:0; font-family:var(--font-body); font-weight:600; font-size:12px; letter-spacing:0.16em; text-transform:uppercase; color:var(--accent-soft); }
.h-rec__wave { display:flex; align-items:center; justify-content:center; gap:3px; height:72px; width:100%; overflow:hidden; }
.h-rec__wave.is-paused { opacity:.5; }
.h-rec__bar { flex:0 0 auto; width:4px; min-height:4px; border-radius:999px; background:var(--accent); }
.h-rec__timer { display:flex; align-items:center; gap:10px; }
.h-rec__pulse { width:10px; height:10px; border-radius:50%; background:#B85032; animation:h-rec-pulse 1.4s ease-in-out infinite; }
.h-rec__time { font-family:var(--font-mono); font-size:32px; font-weight:600; letter-spacing:0.04em; }
.h-rec__controls { display:flex; align-items:center; justify-content:center; gap:28px; }
.h-rec__btn { border:none; border-radius:50%; display:inline-flex; align-items:center; justify-content:center; cursor:pointer; color:var(--white); }
.h-rec__btn--cancel { width:54px; height:54px; background:rgba(255,255,255,0.12); }
.h-rec__btn--pause { width:54px; height:54px; background:rgba(255,255,255,0.12); }
.h-rec__btn--stop { width:78px; height:78px; background:#B85032; box-shadow:var(--shadow-deep); }
.h-rec__square { width:26px; height:26px; border-radius:6px; background:var(--white); }
@keyframes h-rec-pulse { 0%,100% { opacity:1; } 50% { opacity:.3; } }
</style>
