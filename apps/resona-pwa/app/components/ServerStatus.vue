<script setup lang="ts">
import { computed, onMounted } from "vue";
import type { HealthState } from "~/composables/useServerHealth";

// Shared singleton poll loop; the indicator just renders its aggregate. Started
// on mount and left running (the loop self-guards against a double interval).
const health = useServerHealth();

const LABEL: Record<HealthState, string> = {
	up: "Online",
	partial: "Teilweise",
	down: "Offline",
	unknown: "…",
};

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
	const api = `Resona-API: ${reach(health.resonaApi.value)}`;
	if (useRuntimeConfig().public.dataMode === "standalone") {
		return api;
	}
	return `Directus: ${reach(health.directus.value)}\n${api}`;
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
		<span
			class="server-status__dot"
			aria-hidden="true"
		/>
		<span class="server-status__label">{{ label }}</span>
	</button>
</template>

<style scoped>
/* Compact indicator: coloured dot + label, no chip surface (it sits in the header). */
.server-status { display:inline-flex; align-items:center; gap:6px; min-height:32px; padding:4px 8px; border:none; border-radius:var(--r-full); background:none; color:var(--fg3); font-family:var(--font-body); font-size:12px; font-weight:600; cursor:pointer; transition:background-color 150ms ease-out; }
.server-status:hover { background:var(--cream-deep); }
.server-status:active { background:var(--crust-soft); }
.server-status__dot { width:8px; height:8px; border-radius:50%; background:var(--crust); flex-shrink:0; }
.server-status[data-state="up"] .server-status__dot { background:var(--olive); }
.server-status[data-state="partial"] .server-status__dot { background:var(--butter-deep); }
.server-status[data-state="down"] .server-status__dot { background:var(--tomato-deep); }
.server-status[data-state="down"] { color:var(--tomato-deep); }
</style>
