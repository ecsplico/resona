<script setup lang="ts">
import { computed, onMounted } from "vue";
import type { HealthState } from "~/composables/useServerHealth";

// Shared singleton poll loop; the indicator just renders its aggregate. Started
// on mount and left running (the loop self-guards against a double interval).
const health = useServerHealth();

const TONE: Record<HealthState, "success" | "warning" | "neutral" | "danger"> = {
	up: "success",
	partial: "warning",
	down: "danger",
	unknown: "neutral",
};

const LABEL: Record<HealthState, string> = {
	up: "Online",
	partial: "Teilweise",
	down: "Offline",
	unknown: "…",
};

const tone = computed(() => TONE[health.overall.value]);
const label = computed(() => LABEL[health.overall.value]);

function reach(value: boolean | null): string {
	if (value === null) {
		return "—";
	}
	return value ? "erreichbar" : "nicht erreichbar";
}

const title = computed(() => {
	if (!health.online.value) {
		return "Keine Internetverbindung";
	}
	return `Directus: ${reach(health.directus.value)}\nResona-API: ${reach(health.resonaApi.value)}`;
});

onMounted(() => health.start());
</script>

<template>
	<button
		class="server-status"
		type="button"
		:title="title"
		:aria-label="`Serverstatus: ${label}`"
		data-testid="server-status"
		:data-state="health.overall.value"
		@click="health.check()"
	>
		<HChip
			:tone="tone"
			dot
		>
			{{ label }}
		</HChip>
	</button>
</template>

<style scoped>
.server-status { border:none; background:none; padding:0; cursor:pointer; display:inline-flex; }
</style>
