<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import type { Transcript } from "~/types/directus";
import type { TranscriptPatch } from "~/data/types";
import { humanizeError } from "#shared/errors";
import {
	editedSinceProfile,
	postprocessRequest,
	postprocessRunning,
	queueReasonMessage,
	rawRestore,
} from "~/utils/postprocess";

// "Nachbearbeiten": runs a stored hub postprocess profile on the raw transcript
// as a hub text job (Directus mode: the project worker; standalone: the in-browser
// processor). The profile choice is a dialog (`open`, opened from the page's
// menu); inline the panel shows only the run's state and the raw transcript. It
// writes the request onto the transcript row; the result replaces `text` while
// `raw_text` keeps the raw transcript.
const props = defineProps<{
	transcript: Transcript;
	/** Why the actions are unavailable right now (e.g. unsaved edits); null = available. */
	blocked: string | null;
}>();

const emit = defineEmits<{ changed: [] }>();
const open = defineModel<boolean>("open", { default: false });

/** Shown when this device has no hub token: the profile list needs it (and so does
 * the standalone processor that would run the job). */
const NO_TOKEN = "Kein Hub-Token auf diesem Gerät – unter Einstellungen → Verbindung eintragen, dann nachbearbeiten.";
/** Asked before a re-run or restore replaces edits made to a profile's output. */
const REPLACE_EDITS = "Der nachbearbeitete Text wurde seither geändert. Diese Änderungen werden ersetzt (neu aufgenommene Teile bleiben im Rohtranskript). Fortfahren?";

const store = useDataStore();
// Wide screens: a centered dialog instead of a bottom sheet.
const { wide } = useShell();
const sheet = ref<HTMLElement | null>(null);
const tokenGate = useHubTokenGate();
const { profiles, error: profilesError, list } = useProfiles();

const profile = ref("");
const busy = ref(false);
const actionError = ref<string | null>(null);

const running = computed(() => postprocessRunning(props.transcript));
const applied = computed(() => props.transcript.applied_profile ?? null);
const waitingReason = computed(() => queueReasonMessage(props.transcript.postprocess_queue_reason));
const showRaw = computed(() => Boolean(applied.value) && props.transcript.raw_text != null);
const failed = computed(() =>
	props.transcript.postprocess_status === "error" ? props.transcript.postprocess_error ?? "Nachbearbeitung fehlgeschlagen." : null,
);
// Why no run can be requested: the page's reason first, then a missing token or profile list.
const runBlocked = computed(() => {
	if (props.blocked) return props.blocked;
	if (tokenGate.blocked.value) return NO_TOKEN;
	if (profilesError.value) return `Profile nicht verfügbar: ${profilesError.value}`;
	return null;
});

// Preselect the profile last run on this transcript, else the first one the hub lists.
watch(profiles, (names) => {
	if (names.includes(profile.value)) return;
	const last = props.transcript.postprocess_profile ?? applied.value;
	profile.value = last && names.includes(last) ? last : names[0] ?? "";
}, { immediate: true });

onMounted(() => {
	if (!tokenGate.blocked.value) void list();
});

/** False when the user keeps the edits made to a profile's output. */
function replaceEditsConfirmed(): boolean {
	return !editedSinceProfile(props.transcript, props.transcript.text ?? "") || confirm(REPLACE_EDITS);
}

/** Closes the dialog once the request is written; on an error it stays open and shows it. */
async function write(patch: TranscriptPatch): Promise<void> {
	busy.value = true;
	actionError.value = null;
	try {
		await store.updateTranscript(props.transcript.id, patch);
		open.value = false;
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
	if (!profile.value || runBlocked.value !== null || !replaceEditsConfirmed()) return;
	void write(postprocessRequest(profile.value));
}

// Opening clears an old error and puts the focus into the dialog (not on phones:
// a focused select would pop its picker).
watch(open, async (isOpen) => {
	if (!isOpen) return;
	actionError.value = null;
	await nextTick();
	if (wide.value) sheet.value?.querySelector<HTMLElement>("select, button")?.focus();
});

function restore(): void {
	if (!replaceEditsConfirmed()) return;
	void write(rawRestore(props.transcript));
}
</script>

<template>
	<section
		v-if="running || failed || showRaw"
		class="pp"
		data-testid="postprocess"
	>
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
		<details
			v-if="showRaw"
			class="pp__raw"
			data-testid="postprocess-raw"
		>
			<summary>Bearbeitet mit „{{ applied }}“ – Rohtranskript anzeigen</summary>
			<pre class="pp__rawtext">{{ transcript.raw_text }}</pre>
		</details>
	</section>

	<div
		v-if="open"
		class="pp-sheet"
		:class="{ 'pp-sheet--dialog': wide }"
		role="dialog"
		aria-modal="true"
		aria-labelledby="pp-sheet-title"
		data-testid="postprocess-dialog"
		@keydown.esc.prevent="open = false"
	>
		<div
			class="pp-sheet__scrim"
			data-testid="postprocess-scrim"
			@click="open = false"
		/>
		<div
			ref="sheet"
			class="pp-sheet__sheet"
		>
			<p
				id="pp-sheet-title"
				class="pp-sheet__eyebrow"
			>
				NACHBEARBEITEN
			</p>
			<label class="pp__select">
				<span class="pp__label">Profil</span>
				<select
					v-model="profile"
					class="pp__input"
					:disabled="running || busy || profiles.length === 0 || tokenGate.blocked.value || profilesError !== null"
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
			<p
				v-if="runBlocked && !running"
				class="pp__hint"
				data-testid="postprocess-blocked"
			>
				{{ runBlocked }}
			</p>
			<p
				v-if="running"
				class="pp__hint"
			>
				Läuft gerade mit „{{ transcript.postprocess_profile }}“.
			</p>
			<p
				v-if="actionError"
				class="pp__error"
				role="alert"
				data-testid="postprocess-action-error"
			>
				{{ actionError }}
			</p>
			<HButton
				v-if="applied && !running"
				variant="secondary"
				:disabled="busy || blocked !== null"
				data-testid="postprocess-restore"
				@click="restore"
			>
				Rohtranskript wiederherstellen
			</HButton>
			<div class="pp-sheet__actions">
				<HButton
					variant="secondary"
					data-testid="postprocess-cancel"
					@click="open = false"
				>
					Abbrechen
				</HButton>
				<HButton
					variant="primary"
					:disabled="running || busy || !profile || runBlocked !== null"
					data-testid="postprocess-run"
					@click="run"
				>
					{{ running ? "Läuft…" : "Nachbearbeiten" }}
				</HButton>
			</div>
		</div>
	</div>
</template>

<style scoped>
.pp { display:flex; flex-direction:column; gap:8px; margin:0 0 12px; }
.pp__select { display:flex; flex-direction:column; min-width:0; }
.pp__label { font-size:13px; font-weight:600; color:var(--crust-deep); margin-bottom:6px; }
.pp__input { border:1.5px solid var(--border-strong); border-radius:8px; padding:9px 12px; background:var(--white); font-family:var(--font-body); font-size:15px; color:var(--fg1); }
.pp__hint { margin:0; font-size:var(--fs-sm); color:var(--fg2); }
.pp__wait { display:block; margin-top:2px; color:var(--fg1); font-weight:600; }
.pp__error { margin:0; font-size:var(--fs-sm); color:var(--danger); }
.pp__raw summary { cursor:pointer; font-size:var(--fs-sm); color:var(--fg2); }
.pp__rawtext { white-space:pre-wrap; word-break:break-word; margin:8px 0 0; padding:12px; border:1px solid var(--border); border-radius:var(--r-md); background:var(--white); color:var(--fg1); font-family:var(--font-mono); font-size:var(--fs-sm); line-height:1.55; }
/* Same sheet as SaveSheet: bottom sheet on phones, centered dialog when wide. */
.pp-sheet { position:fixed; inset:0; z-index:50; display:flex; flex-direction:column; justify-content:flex-end; }
.pp-sheet__scrim { position:absolute; inset:0; background:rgba(35,25,17,0.72); }
.pp-sheet__sheet { position:relative; background:var(--white); border-radius:var(--r-lg) var(--r-lg) 0 0; padding:28px 24px calc(36px + env(safe-area-inset-bottom)); display:flex; flex-direction:column; gap:16px; }
.pp-sheet--dialog { justify-content:center; align-items:center; padding:24px; }
.pp-sheet--dialog .pp-sheet__sheet { width:100%; max-width:480px; border-radius:var(--r-lg); box-shadow:var(--shadow-deep); animation:pp-sheet-in var(--motion-base) var(--motion-ease); }
@keyframes pp-sheet-in { from { opacity:0; transform:translateY(8px); } }
@media (prefers-reduced-motion:reduce) { .pp-sheet--dialog .pp-sheet__sheet { animation:none; } }
.pp-sheet__eyebrow { margin:0; font-family:var(--font-body); font-weight:600; font-size:12px; letter-spacing:0.16em; color:var(--crust-deep); }
.pp-sheet__actions { display:flex; gap:12px; margin-top:4px; }
.pp-sheet__actions > * { flex:1; }
</style>
