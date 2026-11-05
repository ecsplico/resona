<script setup lang="ts">
const { paneInLayout } = useShell();
</script>

<template>
	<RecordingsPane v-if="!paneInLayout" />
	<section
		v-else
		class="home-empty"
		data-testid="detail-empty"
	>
		<h1 class="home-empty__title">
			Wähle links eine Aufnahme aus
		</h1>
		<p class="home-empty__hint">
			Oder starte eine neue: aufnehmen, eine Audiodatei hochladen oder live mitschreiben.
		</p>
	</section>
</template>

<style scoped>
.home-empty { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:8px; min-height:60vh; padding:32px; text-align:center; }
.home-empty__title { margin:0; font-family:var(--font-display); font-size:var(--fs-xl); font-weight:600; color:var(--fg1); }
.home-empty__hint { margin:0; max-width:40ch; color:var(--fg3); font-size:var(--fs-md); line-height:1.5; }
</style>
