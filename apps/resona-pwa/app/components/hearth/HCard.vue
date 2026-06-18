<script setup lang="ts">
defineProps<{ selected?: boolean }>();
</script>

<template>
	<div
		class="h-card"
		:class="{ 'h-card--selected': selected }"
	>
		<slot />
	</div>
</template>

<style scoped>
.h-card { background:var(--white); border:1px solid var(--border); border-radius:12px; box-shadow:var(--shadow-soft); padding:13px 14px; }
.h-card--selected { background:#FCEFE8; border:1.5px solid var(--accent); }
</style>
