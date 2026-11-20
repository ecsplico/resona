<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { TerminContext } from "~/composables/useTermine";
import { formatTerminWhen, shortText } from "~~/shared/termine";
import type { TerminEvent } from "~~/shared/termine";

// Overrides the resona-pwa layer's empty /live extension point (nuxt.config.ts lists
// this app's components first). Expert mode only: above the transcript the same
// patient's earlier appointments with a short preview of their dictations (newest
// last, next to the recorder), below it the later appointments. Plain text, no
// links: nothing here should lead away from a running dictation.
const props = defineProps<{
	part: "before" | "after";
	eventId: string;
}>();

const PREVIEW_CHARS = 200;

const settings = useHubSettings();
const store = useDataStore();

const context = ref<TerminContext | null>(null);
/** Transcript text per recording id; `null` = none (yet). */
const texts = ref<Record<string, string | null>>({});
let seq = 0;

async function load(): Promise<void> {
	const mine = ++seq;
	if (!settings.expert || !props.eventId) return;
	const loaded = await loadTerminContext(props.eventId);
	if (mine !== seq) return;
	context.value = loaded;
	if (props.part !== "before") return;
	const recordings = loaded.before.flatMap(row => row.dictations);
	await Promise.all(recordings.map(async (recording) => {
		let text: string | null = null;
		try {
			text = (await store.getTranscript(recording.id))?.text ?? null;
		}
		catch {
			// No preview for this one.
		}
		if (mine === seq) texts.value = { ...texts.value, [recording.id]: text };
	}));
}

watch(() => [settings.expert, props.eventId], () => void load(), { immediate: true });

const visible = computed(() => settings.expert && !!context.value);
const before = computed(() => context.value?.before ?? []);
const after = computed<TerminEvent[]>(() => context.value?.after ?? []);
</script>

<template>
	<details
		v-if="visible && part === 'before' && (before.length > 0 || context?.error)"
		class="event-context event-context--before"
		data-testid="live-context-before"
	>
		<summary class="event-context__summary">
			Frühere Diktate ({{ before.length }})
		</summary>
		<p
			v-if="context?.error"
			class="event-context__error"
		>
			{{ context.error }}
		</p>
		<ol class="event-context__list">
			<li
				v-for="row in before"
				:key="row.event.id"
				class="event-context__item"
			>
				<div class="event-context__head">
					<span class="event-context__when">{{ formatTerminWhen(row.event) }}</span>
					<span class="event-context__title">{{ row.event.title }}</span>
				</div>
				<p
					v-for="recording in row.dictations"
					:key="recording.id"
					class="event-context__preview"
				>
					{{ texts[recording.id] ? shortText(texts[recording.id] ?? "", PREVIEW_CHARS) : texts[recording.id] === null ? "Kein Text" : "…" }}
				</p>
			</li>
		</ol>
	</details>

	<details
		v-else-if="visible && part === 'after' && after.length > 0"
		class="event-context event-context--after"
		data-testid="live-context-after"
	>
		<summary class="event-context__summary">
			Folgende Termine ({{ after.length }})
		</summary>
		<ol class="event-context__list">
			<li
				v-for="event in after"
				:key="event.id"
				class="event-context__item"
			>
				<div class="event-context__head">
					<span class="event-context__when">{{ formatTerminWhen(event) }}</span>
					<span class="event-context__title">{{ event.title }}</span>
				</div>
			</li>
		</ol>
	</details>
</template>

<style scoped>
.event-context { margin:0 0 16px; padding:10px 14px; border-radius:var(--r-md); background:var(--night-raised); color:var(--on-night); font-size:var(--fs-sm); line-height:1.45; }
.event-context--after { margin:16px 0 0; }
.event-context__summary { cursor:pointer; font-weight:600; color:var(--on-night-mute); }
.event-context[open] .event-context__summary { margin-bottom:8px; }
.event-context__error { margin:0 0 8px; color:var(--tomato-soft); }
.event-context__list { margin:0; padding:0; list-style:none; display:flex; flex-direction:column; gap:10px; max-height:40vh; overflow-y:auto; }
.event-context__head { display:flex; flex-wrap:wrap; gap:4px 10px; align-items:baseline; }
.event-context__when { font-family:var(--font-mono); font-variant-numeric:tabular-nums; font-size:var(--fs-xs); color:var(--on-night-mute); white-space:nowrap; }
.event-context__title { font-weight:600; overflow-wrap:anywhere; }
.event-context__preview { margin:4px 0 0; color:var(--on-night-mute); white-space:pre-wrap; }
</style>
