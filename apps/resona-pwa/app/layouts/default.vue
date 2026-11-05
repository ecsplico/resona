<script setup lang="ts">
import { useMediaQuery } from "@vueuse/core";

const wide = useMediaQuery("(min-width: 960px)");
provideShell(wide);

const settings = useSettingsDrawer();
</script>

<template>
	<div class="app-shell">
		<AppHeader />
		<div class="app-shell__page">
			<slot />
		</div>
		<SettingsDrawer
			v-if="settings.open.value"
			@close="settings.hide()"
		/>
	</div>
</template>

<style scoped>
.app-shell { min-height:100dvh; background:var(--bg); }
.app-shell__page { max-width:720px; margin:0 auto; }
</style>
