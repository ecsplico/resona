<script setup lang="ts">
type Review = "editing" | "ready";

const props = defineProps<{ modelValue: Review; disabled?: boolean }>();
const emit = defineEmits<{ "update:modelValue": [value: Review] }>();

const OPTIONS: { value: Review; label: string; testid: string }[] = [
	{ value: "editing", label: "In Bearbeitung", testid: "review-editing" },
	{ value: "ready", label: "Freigegeben", testid: "review-ready" },
];

function select(value: Review): void {
	if (!props.disabled && value !== props.modelValue) {
		emit("update:modelValue", value);
	}
}

function onKey(e: KeyboardEvent): void {
	if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
	e.preventDefault();
	const next = props.modelValue === "editing" ? "ready" : "editing";
	select(next);
	const group = e.currentTarget as HTMLElement;
	group.querySelector<HTMLElement>(`[data-value="${next}"]`)?.focus();
}
</script>

<template>
	<div
		class="seg"
		role="radiogroup"
		aria-label="Prüfstatus"
		@keydown="onKey"
	>
		<button
			v-for="opt in OPTIONS"
			:key="opt.value"
			type="button"
			role="radio"
			class="seg__opt"
			:class="{ 'seg__opt--on': modelValue === opt.value }"
			:aria-checked="modelValue === opt.value"
			:tabindex="modelValue === opt.value ? 0 : -1"
			:disabled="disabled"
			:data-value="opt.value"
			:data-testid="opt.testid"
			@click="select(opt.value)"
		>
			{{ opt.label }}
		</button>
	</div>
</template>

<style scoped>
.seg { display:inline-flex; padding:3px; gap:2px; border-radius:999px; background:var(--cream-deep); }
.seg__opt { padding:6px 14px; border:none; border-radius:999px; background:transparent; font-family:var(--font-body); font-size:13px; font-weight:600; color:var(--fg2); cursor:pointer; transition:background var(--motion-fast,150ms) ease-out, color var(--motion-fast,150ms) ease-out; }
.seg__opt:hover:not(:disabled) { color:var(--fg1); }
.seg__opt--on { background:var(--white); color:var(--accent-ink,var(--accent)); box-shadow:var(--shadow-soft); }
.seg__opt:disabled { cursor:default; opacity:0.6; }
</style>
