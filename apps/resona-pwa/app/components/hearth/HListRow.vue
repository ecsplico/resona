<script setup lang="ts">
defineProps<{
	title: string;
	meta?: string;
	status: "pending" | "transcribing" | "done" | "error";
	preview?: string;
	selected?: boolean;
}>();

defineEmits<{ select: [] }>();
</script>

<template>
	<HCard
		class="h-listrow"
		:selected="selected"
		@click="$emit('select')"
	>
		<div class="h-listrow__top">
			<div class="h-listrow__main">
				<div class="h-listrow__title">
					{{ title }}
				</div>
				<div
					v-if="meta"
					class="h-listrow__meta"
				>
					{{ meta }}
				</div>
			</div>
			<div class="h-listrow__side">
				<slot name="actions" />
				<StatusChip :status="status" />
			</div>
		</div>
		<div
			v-if="preview"
			class="h-listrow__preview"
		>
			{{ preview }}
		</div>
	</HCard>
</template>

<style scoped>
.h-listrow { cursor:pointer; }
.h-listrow__top { display:flex; align-items:flex-start; }
.h-listrow__main { flex:1; min-width:0; }
.h-listrow__title { font-family:var(--font-display); font-size:17px; font-weight:600; color:var(--fg1); line-height:1.2; }
.h-listrow__meta { font-family:var(--font-mono); font-size:12px; color:var(--fg3); margin-top:5px; }
.h-listrow__side { display:flex; align-items:center; gap:6px; }
.h-listrow__preview { font-style:italic; font-size:13px; color:var(--fg3); border-top:1px solid var(--border); padding-top:10px; margin-top:10px; }
</style>
