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
	<HCard
		class="queued-row"
		data-testid="queued-item"
	>
		<div class="queued-row__top">
			<div class="queued-row__main">
				<div class="queued-row__title">
					{{ item.title || "Aufnahme" }}
				</div>
				<div
					v-if="meta"
					class="queued-row__meta"
				>
					{{ meta }}
				</div>
			</div>
			<HChip tone="warning">
				Wartet (offline)
			</HChip>
		</div>
	</HCard>
</template>

<style scoped>
.queued-row__top { display:flex; align-items:flex-start; }
.queued-row__main { flex:1; min-width:0; }
.queued-row__title { font-family:var(--font-display); font-size:17px; font-weight:600; color:var(--fg1); line-height:1.2; }
.queued-row__meta { font-family:var(--font-mono); font-size:12px; color:var(--fg3); margin-top:5px; }
</style>
