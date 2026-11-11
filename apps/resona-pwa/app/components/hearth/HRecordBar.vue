<script setup lang="ts">
import { computed } from "vue";

// Docked record bar for re-dictation: takes HPlayerDock's place at the bottom of the
// detail page so the transcript stays visible. Same buttons and order as HRecordingSheet.
const props = withDefaults(defineProps<{
	elapsed: number;
	levels: number[];
	paused?: boolean;
	label?: string;
	// Parts recorded earlier that are still being transcribed.
	pending?: number;
	// German reason while the recording is interrupted: the center button continues with a new part.
	interruption?: string | null;
}>(), {
	paused: false,
	label: "Nachdiktat",
	pending: 0,
	interruption: null,
});

const emit = defineEmits<{ pause: []; resume: []; stop: []; cancel: []; continue: [] }>();

const halted = computed(() => props.paused || Boolean(props.interruption));

// Compact meter: only the newest bars that fit next to the controls on a 390 px phone (5 px each incl. gap).
const MAX_BARS = 24;
// Speech RMS sits around 0.05-0.3, so bars are scaled up to use the meter's height.
const LEVEL_GAIN = 3;

const bars = computed(() => props.levels.slice(-MAX_BARS).map(level => Math.max(12, Math.round(Math.min(1, level * LEVEL_GAIN) * 100))));

const pendingText = computed(() => {
	const n = props.pending;
	if (!Number.isFinite(n) || n <= 0) {
		return "";
	}
	return n === 1 ? "1 Teil wird transkribiert" : `${n} Teile werden transkribiert`;
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
	if (props.interruption) {
		emit("continue");
	}
	else if (props.paused) {
		emit("resume");
	}
	else {
		emit("pause");
	}
}
</script>

<template>
	<div
		class="h-rbar"
		:class="{ 'is-paused': halted }"
		role="region"
		:aria-label="label"
		data-testid="record-bar"
	>
		<div class="h-rbar__row">
			<div class="h-rbar__info">
				<span
					class="h-rbar__label"
					data-testid="record-bar-label"
				>{{ interruption ? "Unterbrochen" : label }}</span>
				<span class="h-rbar__timer">
					<span
						class="h-rbar__pulse"
						aria-hidden="true"
					/>
					<span
						class="h-rbar__time"
						data-testid="timer"
					>{{ formatTime(elapsed) }}</span>
					<span
						v-if="paused"
						class="h-rbar__sr"
					>pausiert</span>
				</span>
			</div>

			<div
				class="h-rbar__meter"
				aria-hidden="true"
			>
				<span
					v-for="(height, i) in bars"
					:key="i"
					class="h-rbar__bar"
					data-testid="wave-bar"
					:style="{ height: height + '%' }"
				/>
			</div>

			<div class="h-rbar__controls">
				<button
					type="button"
					class="h-rbar__btn h-rbar__btn--small"
					data-testid="cancel"
					aria-label="Verwerfen"
					title="Verwerfen"
					@click="emit('cancel')"
				>
					<svg
						width="16"
						height="16"
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
					type="button"
					class="h-rbar__btn h-rbar__btn--pause"
					:class="{ 'is-paused': halted }"
					data-testid="pause"
					:aria-label="halted ? 'Fortsetzen' : 'Pausieren'"
					:title="halted ? 'Fortsetzen' : 'Pausieren'"
					@click="togglePause"
				>
					<svg
						v-if="halted"
						width="22"
						height="22"
						viewBox="0 0 24 24"
						fill="currentColor"
						aria-hidden="true"
					>
						<path d="M8 5v14l11-7z" />
					</svg>
					<svg
						v-else
						width="22"
						height="22"
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

				<button
					type="button"
					class="h-rbar__btn h-rbar__btn--small"
					data-testid="stop"
					:aria-label="interruption ? 'Beenden' : 'Aufnahme beenden'"
					:title="interruption ? 'Beenden' : 'Aufnahme beenden'"
					@click="emit('stop')"
				>
					<span class="h-rbar__square" />
				</button>
			</div>
		</div>

		<p
			v-if="interruption"
			class="h-rbar__notice"
			role="alert"
			data-testid="interruption"
		>
			{{ interruption }}
		</p>
		<!-- Always rendered so screen readers announce changes; empty while nothing is pending. -->
		<p
			class="h-rbar__status"
			:class="{ 'is-empty': !pendingText }"
			role="status"
			aria-live="polite"
			data-testid="record-bar-pending"
		>
			{{ pendingText }}
		</p>
	</div>
</template>

<style scoped>
.h-rbar { background:var(--night); color:var(--on-night); border-top:1px solid var(--border); padding:12px 16px calc(14px + env(safe-area-inset-bottom, 0px)); display:flex; flex-direction:column; }
.h-rbar__row { display:flex; align-items:center; gap:12px; min-width:0; }
.h-rbar__info { flex:0 0 auto; display:flex; flex-direction:column; gap:2px; min-width:0; max-width:96px; }
.h-rbar__label { font-family:var(--font-body); font-weight:600; font-size:11px; letter-spacing:0.12em; text-transform:uppercase; color:var(--accent-soft); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.h-rbar__timer { display:flex; align-items:center; gap:6px; }
.h-rbar__pulse { flex:0 0 auto; width:8px; height:8px; border-radius:50%; background:var(--tomato-deep); animation:h-rbar-pulse 1.4s ease-in-out infinite; }
.h-rbar__time { font-family:var(--font-mono); font-size:16px; font-weight:600; letter-spacing:0.04em; font-variant-numeric:tabular-nums; }
.h-rbar__sr { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }
/* Newest bars on the right; older ones are clipped on the left when space runs out. */
.h-rbar__meter { flex:1 1 auto; min-width:0; height:32px; display:flex; align-items:center; justify-content:flex-end; gap:2px; overflow:hidden; }
.h-rbar__bar { flex:0 0 auto; width:3px; border-radius:999px; background:var(--accent); }
.h-rbar__controls { flex:0 0 auto; display:flex; align-items:center; gap:8px; }
.h-rbar__btn { border:none; border-radius:50%; display:inline-flex; align-items:center; justify-content:center; cursor:pointer; color:var(--white); padding:0; }
.h-rbar__btn--small { width:40px; height:40px; background:rgba(255,255,255,0.12); }
/* Primary: pauses/resumes; accent while paused, showing play. */
.h-rbar__btn--pause { width:52px; height:52px; background:var(--tomato-deep); box-shadow:var(--shadow-lift); }
.h-rbar__btn--pause.is-paused { background:var(--accent); }
.h-rbar__btn:focus-visible { outline:2px solid var(--accent-soft); outline-offset:2px; }
.h-rbar__square { width:14px; height:14px; border-radius:3px; background:var(--white); }
/* Never display:none: a hidden live region may not announce when it gets text. Empty, it takes no height. */
.h-rbar__status { margin:6px 0 0; font-family:var(--font-body); font-size:var(--fs-xs); color:var(--on-night-mute); }
.h-rbar__status.is-empty { margin-top:0; }
.h-rbar__notice { margin:8px 0 0; font-family:var(--font-body); font-size:var(--fs-sm); line-height:1.4; color:var(--on-night); }
/* Paused: meter and pulse dimmed, pulse stops. */
.h-rbar.is-paused .h-rbar__meter { opacity:.4; }
.h-rbar.is-paused .h-rbar__pulse { animation:none; opacity:.35; }
@keyframes h-rbar-pulse { 0%,100% { opacity:1; } 50% { opacity:.3; } }
@media (prefers-reduced-motion:reduce) { .h-rbar__pulse { animation:none; } }
</style>
