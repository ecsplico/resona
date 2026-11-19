<script setup lang="ts">
import { computed, watch } from "vue";
import { useMediaQuery } from "@vueuse/core";

// Replaces the layer's default layout: VerlaufHeader instead of AppHeader, and the
// home page (`/`) owns its whole width. Only the recording detail keeps the layer's
// desktop workspace with the recordings pane beside it.
const wide = useMediaQuery("(min-width: 960px)");
const { paneInLayout } = provideShell(wide);

const settings = useSettingsDrawer();
const route = useRoute();
const showAside = computed(() => paneInLayout.value && route.path !== "/");

// The drawer belongs to the page it was opened on.
watch(() => route.path, () => settings.hide());
</script>

<template>
	<div
		class="app-shell"
		:class="{ 'app-shell--panes': showAside }"
	>
		<VerlaufHeader />
		<div
			class="app-shell__body"
			:data-testid="showAside ? 'workspace' : undefined"
		>
			<aside
				v-if="showAside"
				class="app-shell__aside"
				aria-label="Aufnahmen"
			>
				<RecordingsPane />
			</aside>
			<main class="app-shell__main">
				<slot />
			</main>
		</div>
		<SettingsDrawer
			v-if="settings.open.value"
			@close="settings.hide()"
		/>
	</div>
</template>

<style scoped>
.app-shell { display:flex; flex-direction:column; min-height:100dvh; background:var(--bg); }
.app-shell__body { flex:1; display:flex; flex-direction:column; min-width:0; }
.app-shell__main { flex:1; display:flex; flex-direction:column; width:100%; min-width:0; }

.app-shell--panes { height:100dvh; }
.app-shell--panes .app-shell__body { min-height:0; display:grid; grid-template-columns:clamp(360px, 28vw, 400px) minmax(0, 1fr); }
.app-shell__aside { min-height:0; overflow-y:auto; overscroll-behavior:contain; background:var(--bg-panel); border-right:1px solid var(--border); }
.app-shell--panes .app-shell__main { min-height:0; overflow-y:auto; }
</style>
