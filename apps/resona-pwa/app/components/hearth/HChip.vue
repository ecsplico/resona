<script setup lang="ts">
defineProps<{ tone?: "success" | "warning" | "neutral" | "danger"; dot?: boolean }>();
</script>

<template>
	<span
		class="h-chip"
		:class="`h-chip--${tone ?? 'neutral'}`"
	>
		<span
			v-if="dot"
			class="h-chip__dot"
		/>
		<slot />
	</span>
</template>

<style scoped>
.h-chip { display:inline-flex; align-items:center; gap:5px; padding:4px 9px; border-radius:999px; font-family:var(--font-body); font-weight:600; font-size:11px; }
.h-chip__dot { width:6px; height:6px; border-radius:50%; background:currentColor; }
.h-chip--success { background:var(--olive-soft); color:var(--olive-deep); }
.h-chip--warning { background:var(--butter-soft); color:#8B6200; }
.h-chip--neutral { background:var(--crust-soft); color:var(--crust-deep); }
.h-chip--danger { background:var(--tomato-soft); color:var(--tomato-deep); }
</style>
