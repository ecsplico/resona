<script setup lang="ts">
defineProps<{ variant?: "primary" | "secondary"; disabled?: boolean }>();
</script>

<template>
	<button
		class="h-btn"
		:class="`h-btn--${variant ?? 'primary'}`"
		:disabled="disabled"
	>
		<slot />
	</button>
</template>

<style scoped>
.h-btn { border:none; border-radius:10px; padding:14px 16px; font-family:var(--font-body); font-weight:600; font-size:15px; cursor:pointer; }
.h-btn--primary { background:var(--accent); color:var(--fg-on-accent); }
.h-btn--secondary { background:transparent; color:var(--fg2); border:1.5px solid var(--border-strong); }
.h-btn:disabled { opacity:.5; cursor:default; }
</style>
