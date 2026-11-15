<script setup lang="ts">
// The dock's record button. A tap emits `tap`; holding it for `HOLD_MS` emits
// `hold-start`, and releasing it after that `hold-end`. A keyboard press (Enter,
// Space) is a tap. `press` comes first on every pointer press, before any focus
// moves; `mousedown` is prevented, so the editor keeps its focus and caret anyway.
import { onBeforeUnmount } from "vue";

const props = defineProps<{ active?: boolean; busy?: boolean; disabled?: boolean; label: string }>();
const emit = defineEmits<{ "press": []; "tap": []; "hold-start": []; "hold-end": [] }>();

const HOLD_MS = 400;
let timer: ReturnType<typeof setTimeout> | null = null;
let held = false;

function clearTimer(): boolean {
	if (timer === null) return false;
	clearTimeout(timer);
	timer = null;
	return true;
}

function onDown(e: PointerEvent): void {
	if (props.disabled || e.button > 0) return;
	try {
		(e.currentTarget as Element | null)?.setPointerCapture?.(e.pointerId);
	}
	catch {
		// pointer capture unsupported (test env)
	}
	emit("press");
	held = false;
	clearTimer();
	timer = setTimeout(() => {
		timer = null;
		held = true;
		emit("hold-start");
	}, HOLD_MS);
}

function onUp(): void {
	if (clearTimer()) emit("tap");
	else if (held) {
		held = false;
		emit("hold-end");
	}
}

// A cancelled press (scroll, system gesture) is no tap; a running hold ends.
function onCancel(): void {
	clearTimer();
	if (held) {
		held = false;
		emit("hold-end");
	}
}

// Pointer presses are handled above; `detail === 0` is a keyboard activation.
function onClick(e: MouseEvent): void {
	if (e.detail === 0 && !props.disabled) emit("tap");
}

onBeforeUnmount(onCancel);
</script>

<template>
	<button
		type="button"
		class="h-rec"
		:class="{ 'h-rec--active': active, 'h-rec--busy': busy }"
		:disabled="disabled"
		:aria-label="label"
		:title="label"
		:aria-pressed="active"
		data-testid="record"
		@pointerdown.prevent="onDown"
		@pointerup="onUp"
		@pointercancel="onCancel"
		@click="onClick"
		@contextmenu.prevent
	>
		<svg
			width="24"
			height="24"
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
				y2="21"
			/>
		</svg>
	</button>
</template>

<style scoped>
.h-rec { width:52px; height:52px; flex:0 0 auto; border:none; border-radius:50%; background:var(--accent); color:var(--white); display:inline-flex; align-items:center; justify-content:center; cursor:pointer; box-shadow:var(--shadow-lift); touch-action:none; user-select:none; -webkit-user-select:none; -webkit-touch-callout:none; transition:background var(--motion-fast) var(--motion-ease), transform var(--motion-fast) var(--motion-ease); }
.h-rec:hover:not(:disabled) { background:var(--accent-hover); }
.h-rec--active { background:var(--accent-hover); transform:scale(1.08); box-shadow:0 0 0 6px var(--accent-soft), var(--shadow-lift); }
.h-rec--busy { opacity:0.65; cursor:default; }
.h-rec:disabled:not(.h-rec--busy) { opacity:0.45; cursor:default; }
@media (prefers-reduced-motion:reduce) { .h-rec { transition:none; } .h-rec--active { transform:none; } }
</style>
