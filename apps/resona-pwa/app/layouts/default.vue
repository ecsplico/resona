<script setup lang="ts">
import { useMediaQuery } from "@vueuse/core";

const wide = useMediaQuery("(min-width: 960px)");
const { paneInLayout } = provideShell(wide);

const settings = useSettingsDrawer();
</script>

<template>
	<div
		class="app-shell"
		:class="{ 'app-shell--panes': paneInLayout }"
	>
		<AppHeader />
		<!-- Desktop workspace on / and /recording/:id: the recordings pane lives here,
			not in the page, so navigating between those routes keeps its filters and scroll. -->
		<div
			v-if="paneInLayout"
			class="app-shell__panes"
			data-testid="workspace"
		>
			<aside
				class="app-shell__aside"
				aria-label="Aufnahmen"
			>
				<RecordingsPane />
			</aside>
			<main class="app-shell__main">
				<slot />
			</main>
		</div>
		<main
			v-else
			class="app-shell__page"
		>
			<slot />
		</main>
		<SettingsDrawer
			v-if="settings.open.value"
			@close="settings.hide()"
		/>
	</div>
</template>

<style scoped>
.app-shell { display:flex; flex-direction:column; min-height:100dvh; background:var(--bg); }
/* Pages run full width and center their own column. */
.app-shell__page { flex:1; display:flex; flex-direction:column; width:100%; min-width:0; }

/* Two panes fill the viewport below the header and scroll independently. */
.app-shell--panes { height:100dvh; }
.app-shell__panes { flex:1; min-height:0; display:grid; grid-template-columns:clamp(360px, 28vw, 400px) minmax(0, 1fr); }
.app-shell__aside { min-height:0; overflow-y:auto; overscroll-behavior:contain; background:var(--bg-panel); border-right:1px solid var(--border); }
.app-shell__main { min-width:0; min-height:0; overflow-y:auto; display:flex; flex-direction:column; }
</style>
