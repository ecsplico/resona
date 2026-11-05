<script setup lang="ts">
import type { QueuedItem } from "~/composables/useOfflineQueue";

const props = defineProps<{ item: QueuedItem }>();

function formatDuration(seconds?: number): string {
	if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) {
		return "";
	}
	const total = Math.round(seconds);
	const minutes = Math.floor(total / 60);
	const secs = total % 60;
	return `${minutes}:${secs.toString().padStart(2, "0")}`;
}

const meta = computed(() => {
	const parts = [
		formatDuration(props.item.duration_seconds),
		props.item.language ? props.item.language.toUpperCase() : "",
	].filter(part => part.length > 0);
	return parts.join(" · ");
});
</script>

<template>
	<div
		class="queued-row"
		data-testid="queued-item"
	>
		<div class="queued-row__main">
			<span class="queued-row__title">{{ item.title || "Aufnahme" }}</span>
			<span
				v-if="meta"
				class="queued-row__meta"
			>{{ meta }}</span>
		</div>
		<HChip tone="warning">
			Wartet (offline)
		</HChip>
	</div>
</template>

<style scoped>
.queued-row { display:flex; align-items:center; gap:8px; min-height:60px; padding:12px 16px; background:var(--bg-raised); }
.queued-row__main { flex:1; min-width:0; display:flex; flex-direction:column; gap:3px; }
.queued-row__title { font-family:var(--font-display); font-size:var(--fs-md); font-weight:600; line-height:1.25; color:var(--fg1); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.queued-row__meta { font-family:var(--font-mono); font-size:var(--fs-xs); font-variant-numeric:tabular-nums; line-height:1.4; color:var(--fg3); }
</style>
