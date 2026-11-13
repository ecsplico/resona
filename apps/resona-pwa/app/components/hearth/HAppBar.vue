<script setup lang="ts">
defineProps<{ tone?: "dark" | "light"; title?: string }>();
</script>

<template>
	<div
		class="h-appbar"
		:class="`h-appbar--${tone ?? 'dark'}`"
	>
		<div class="h-appbar__leading">
			<slot name="leading" />
		</div>
		<div class="h-appbar__title">
			<slot>{{ title }}</slot>
		</div>
		<div class="h-appbar__trailing">
			<slot name="trailing" />
		</div>
	</div>
</template>

<style scoped>
.h-appbar { display:flex; align-items:center; gap:12px; padding:16px 20px; padding-top:calc(16px + env(safe-area-inset-top)); }
.h-appbar--dark { background:var(--accent); color:var(--white); }
.h-appbar--light { background:var(--cream); color:var(--fg1); }
.h-appbar__title { font-family:var(--font-display); font-weight:600; font-size:20px; }
.h-appbar__trailing { margin-left:auto; display:flex; align-items:center; gap:8px; }
</style>
