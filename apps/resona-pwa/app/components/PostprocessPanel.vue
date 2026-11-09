<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import type { Transcript } from "~/types/directus";
import type { TranscriptPatch } from "~/data/types";
import { humanizeError } from "#shared/errors";
import {
	postprocessRequest,
	postprocessRunning,
	queueReasonMessage,
	rawRestore,
} from "~/utils/postprocess";

// "Nachbearbeiten": runs a stored hub postprocess profile on the raw transcript
// as a hub text job (Directus mode: the project worker; standalone: the in-browser
// processor). This panel only writes the request onto the transcript row; the
// result replaces `text` while `raw_text` keeps the raw transcript.
const props = defineProps<{
	transcript: Transcript;
	/** Why the actions are unavailable right now (e.g. unsaved edits); null = available. */
	blocked: string | null;
}>();

const emit = defineEmits<{ changed: [] }>();

const store = useDataStore();
const { profiles, list } = useProfiles();

const profile = ref("");
const busy = ref(false);
const actionError = ref<string | null>(null);

const running = computed(() => postprocessRunning(props.transcript));
const applied = computed(() => props.transcript.applied_profile ?? null);
const waitingReason = computed(() => queueReasonMessage(props.transcript.postprocess_queue_reason));
const failed = computed(() =>
	props.transcript.postprocess_status === "error" ? props.transcript.postprocess_error ?? "Nachbearbeitung fehlgeschlagen." : null,
);

// Preselect the profile last run on this transcript, else the first one the hub lists.
watch(profiles, (names) => {
	if (names.includes(profile.value)) return;
	const last = props.transcript.postprocess_profile ?? applied.value;
	profile.value = last && names.includes(last) ? last : names[0] ?? "";
}, { immediate: true });

onMounted(() => {
	void list();
});

async function write(patch: TranscriptPatch): Promise<void> {
	busy.value = true;
	actionError.value = null;
	try {
		await store.updateTranscript(props.transcript.id, patch);
		emit("changed");
	}
	catch (err) {
		actionError.value = humanizeError(err, "Speichern fehlgeschlagen.");
	}
	finally {
		busy.value = false;
	}
}

function run(): void {
	if (!profile.value) return;
	void write(postprocessRequest(profile.value));
}

function restore(): void {
	void write(rawRestore(props.transcript));
}
</script>

<template>
	<section
		class="pp"
		data-testid="postprocess"
	>
		<div class="pp__row">
			<label class="pp__select">
				<span class="pp__label">Nachbearbeiten mit</span>
				<select
					v-model="profile"
					class="pp__input"
					:disabled="running || busy || profiles.length === 0"
					data-testid="postprocess-profile"
				>
					<option
						v-for="name in profiles"
						:key="name"
						:value="name"
					>
						{{ name }}
					</option>
				</select>
			</label>
			<HButton
				variant="secondary"
				:disabled="running || busy || !profile || blocked !== null"
				data-testid="postprocess-run"
				@click="run"
			>
				{{ running ? "Läuft…" : "Nachbearbeiten" }}
			</HButton>
			<HButton
				v-if="applied && !running"
				variant="secondary"
				:disabled="busy || blocked !== null"
				data-testid="postprocess-restore"
				@click="restore"
			>
				Rohtranskript wiederherstellen
			</HButton>
		</div>

		<p
			v-if="blocked && !running"
			class="pp__hint"
			data-testid="postprocess-blocked"
		>
			{{ blocked }}
		</p>
		<p
			v-if="running"
			class="pp__hint"
			role="status"
			aria-live="polite"
			data-testid="postprocess-running"
		>
			Wird mit „{{ transcript.postprocess_profile }}“ nachbearbeitet – der Text ist so lange gesperrt.
			<span
				v-if="waitingReason"
				class="pp__wait"
				data-testid="postprocess-queue"
			>{{ waitingReason }}</span>
		</p>
		<p
			v-else-if="failed"
			class="pp__error"
			role="alert"
			data-testid="postprocess-error"
		>
			{{ failed }}
		</p>
		<p
			v-if="actionError"
			class="pp__error"
			role="alert"
			data-testid="postprocess-action-error"
		>
			{{ actionError }}
		</p>
		<details
			v-if="applied && transcript.raw_text != null"
			class="pp__raw"
			data-testid="postprocess-raw"
		>
			<summary>Bearbeitet mit „{{ applied }}“ – Rohtranskript anzeigen</summary>
			<pre class="pp__rawtext">{{ transcript.raw_text }}</pre>
		</details>
	</section>
</template>

<style scoped>
.pp { display:flex; flex-direction:column; gap:8px; margin:0 0 12px; }
.pp__row { display:flex; align-items:flex-end; gap:8px; flex-wrap:wrap; }
.pp__select { display:flex; flex-direction:column; min-width:0; }
.pp__label { font-size:13px; font-weight:600; color:var(--crust-deep); margin-bottom:6px; }
.pp__input { border:1.5px solid var(--border-strong); border-radius:8px; padding:9px 12px; background:var(--white); font-family:var(--font-body); font-size:15px; color:var(--fg1); }
.pp__hint { margin:0; font-size:var(--fs-sm); color:var(--fg2); }
.pp__wait { display:block; margin-top:2px; color:var(--fg1); font-weight:600; }
.pp__error { margin:0; font-size:var(--fs-sm); color:var(--danger); }
.pp__raw summary { cursor:pointer; font-size:var(--fs-sm); color:var(--fg2); }
.pp__rawtext { white-space:pre-wrap; word-break:break-word; margin:8px 0 0; padding:12px; border:1px solid var(--border); border-radius:var(--r-md); background:var(--white); color:var(--fg1); font-family:var(--font-mono); font-size:var(--fs-sm); line-height:1.55; }
</style>
