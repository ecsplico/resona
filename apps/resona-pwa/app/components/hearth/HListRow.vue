<script setup lang="ts">
const props = defineProps<{
	title: string;
	meta?: string;
	status?: "pending" | "transcribing" | "done" | "error";
	preview?: string;
	selected?: boolean;
}>();

defineEmits<{ select: [] }>();

// Only states that need attention get a chip; a finished row stays quiet.
const attentionStatus = computed(() => (props.status && props.status !== "done" ? props.status : null));
</script>

<template>
	<div
		class="h-listrow"
		:class="{ 'h-listrow--selected': selected }"
		@click="$emit('select')"
	>
		<button
			type="button"
			class="h-listrow__hit"
			:aria-current="selected ? 'page' : undefined"
		>
			<span class="h-listrow__title">{{ title }}</span>
			<span
				v-if="meta"
				class="h-listrow__meta"
			>{{ meta }}</span>
			<span
				v-if="preview"
				class="h-listrow__preview"
			>{{ preview }}</span>
		</button>
		<div
			v-if="attentionStatus || $slots.actions"
			class="h-listrow__side"
		>
			<StatusChip
				v-if="attentionStatus"
				:status="attentionStatus"
			/>
			<slot name="actions" />
		</div>
	</div>
</template>

<style scoped>
.h-listrow { display:flex; align-items:center; gap:8px; min-height:60px; background:var(--bg-raised); cursor:pointer; transition:background-color var(--motion-fast) var(--motion-ease); }
.h-listrow:hover { background:var(--row-hover); }
.h-listrow:active { background:var(--row-active); }
.h-listrow:has(.h-listrow__hit:focus-visible) { box-shadow:inset 0 0 0 3px var(--focus-ring); }
.h-listrow--selected, .h-listrow--selected:hover { background:var(--accent-tint); }
.h-listrow__hit { flex:1; min-width:0; display:flex; flex-direction:column; gap:3px; padding:12px 0 12px 16px; text-align:left; background:none; border:0; color:inherit; cursor:pointer; }
.h-listrow__hit:focus-visible { outline:none; }
.h-listrow__hit:last-child { padding-right:16px; }
.h-listrow__title { font-family:var(--font-display); font-size:var(--fs-md); font-weight:600; line-height:1.25; color:var(--fg1); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; transition:color var(--motion-fast) var(--motion-ease); }
.h-listrow--selected .h-listrow__title { color:var(--accent-ink); }
.h-listrow__meta { font-family:var(--font-mono); font-size:var(--fs-xs); font-variant-numeric:tabular-nums; line-height:1.4; color:var(--fg3); }
.h-listrow__side { flex-shrink:0; display:flex; align-items:center; gap:4px; padding-right:12px; }
.h-listrow__preview { font-size:var(--fs-sm); font-style:italic; line-height:1.4; color:var(--fg3); display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
</style>
