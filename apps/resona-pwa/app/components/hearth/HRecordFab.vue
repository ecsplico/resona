<script setup lang="ts">
defineEmits<{ click: [] }>();
</script>

<template>
	<button
		type="button"
		class="h-fab"
		aria-label="Aufnahme starten"
		@click="$emit('click')"
	>
		<span class="h-fab__ring">
			<span class="h-fab__dot" />
		</span>
	</button>
</template>

<style scoped>
.h-fab { width:74px; height:74px; border:none; border-radius:50%; background:var(--accent); display:inline-flex; align-items:center; justify-content:center; cursor:pointer; box-shadow:var(--shadow-lift); }
.h-fab:hover { background:var(--accent-hover); }
.h-fab__ring { width:54px; height:54px; border-radius:50%; border:3px solid var(--white); display:inline-flex; align-items:center; justify-content:center; }
.h-fab__dot { width:24px; height:24px; border-radius:50%; background:var(--white); }
</style>
