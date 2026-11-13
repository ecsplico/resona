<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import type { Ref } from "vue";
import type { SaveRequest } from "~/data/types";
import { placeholderTitle } from "~/utils/titleSuggestion";

// `count` > 1: one sheet for several shared recordings; each keeps its file name
// as title, so there is no title field.
const props = withDefaults(
	defineProps<{ initialTitle?: string; count?: number }>(),
	{ initialTitle: "", count: 1 },
);

const emit = defineEmits<{
	save: [SaveRequest];
	discard: [];
}>();

const cfg = useRuntimeConfig().public;

const title = ref(props.initialTitle);
// A later share replaces the proposal while the sheet is still open.
watch(() => props.initialTitle, (next) => {
	title.value = next;
});
const language = ref(cfg.defaultLanguage);
const profile = ref(cfg.defaultProfile);
const transcribe = ref(true);

const profileOptions: Ref<string[]> = ref([cfg.defaultProfile]);
const languageOptions = ["de", "en"];

const { list } = useProfiles();

// Wide screens: a centered dialog instead of a bottom sheet.
const { wide } = useShell();
const sheet = ref<HTMLElement | null>(null);

onMounted(async () => {
	if (wide.value) {
		// Keyboard users start in the title field; on phones this would pop the keyboard.
		sheet.value?.querySelector<HTMLInputElement>("[data-testid=\"title\"] input")?.focus();
	}
	const names = await list();
	if (names.length > 0) {
		profileOptions.value = names;
		if (!names.includes(profile.value)) {
			profile.value = names[0]!;
		}
	}
});

function submit(): void {
	const typed = title.value.trim();
	emit("save", {
		title: typed || placeholderTitle(new Date()),
		// Only a transcribed recording gets a title derived from its content.
		titleAuto: typed ? false : transcribe.value,
		language: language.value,
		profile: profile.value,
		transcribe: transcribe.value,
	});
}
</script>

<template>
	<div
		class="save-sheet"
		:class="{ 'save-sheet--dialog': wide }"
		role="dialog"
		aria-modal="true"
		aria-labelledby="save-sheet-title"
	>
		<div class="save-sheet__scrim" />
		<div
			ref="sheet"
			class="save-sheet__sheet"
		>
			<p
				id="save-sheet-title"
				class="save-sheet__eyebrow"
			>
				{{ count > 1 ? `${count} AUFNAHMEN SPEICHERN` : "AUFNAHME SPEICHERN" }}
			</p>

			<HField
				v-if="count <= 1"
				v-model="title"
				:placeholder="transcribe ? 'Automatisch aus dem Inhalt' : 'Datum und Uhrzeit'"
				class="save-sheet__title"
				label="Titel"
				data-testid="title"
			/>

			<div class="save-sheet__row">
				<label class="save-sheet__toggle-label">Transkribieren</label>
				<HToggle
					v-model="transcribe"
					data-testid="transcribe-toggle"
				/>
			</div>

			<template v-if="transcribe">
				<label class="save-sheet__select">
					<span class="save-sheet__select-label">Sprache</span>
					<select
						v-model="language"
						class="save-sheet__select-input"
						data-testid="language-select"
					>
						<option
							v-for="opt in languageOptions"
							:key="opt"
							:value="opt"
						>
							{{ opt }}
						</option>
					</select>
				</label>

				<label class="save-sheet__select">
					<span class="save-sheet__select-label">Profil</span>
					<select
						v-model="profile"
						class="save-sheet__select-input"
						data-testid="profile-select"
					>
						<option
							v-for="opt in profileOptions"
							:key="opt"
							:value="opt"
						>
							{{ opt }}
						</option>
					</select>
				</label>
			</template>

			<div class="save-sheet__actions">
				<HButton
					variant="secondary"
					data-testid="discard"
					@click="emit('discard')"
				>
					Verwerfen
				</HButton>
				<HButton
					variant="primary"
					data-testid="save"
					@click="submit"
				>
					Speichern
				</HButton>
			</div>
		</div>
	</div>
</template>

<style scoped>
.save-sheet { position:fixed; inset:0; z-index:50; display:flex; flex-direction:column; justify-content:flex-end; }
.save-sheet__scrim { position:absolute; inset:0; background:rgba(35,25,17,0.72); }
.save-sheet__sheet { position:relative; background:var(--white); border-radius:var(--r-lg) var(--r-lg) 0 0; padding:28px 24px calc(36px + env(safe-area-inset-bottom)); display:flex; flex-direction:column; gap:18px; }
/* Wide: centered dialog, max 480 px, rises in on open. */
.save-sheet--dialog { justify-content:center; align-items:center; padding:24px; }
.save-sheet--dialog .save-sheet__sheet { width:100%; max-width:480px; border-radius:var(--r-lg); box-shadow:var(--shadow-deep); animation:save-sheet-in var(--motion-base) var(--motion-ease); }
@keyframes save-sheet-in { from { opacity:0; transform:translateY(8px); } }
@media (prefers-reduced-motion:reduce) { .save-sheet--dialog .save-sheet__sheet { animation:none; } }
.save-sheet__eyebrow { margin:0; font-family:var(--font-body); font-weight:600; font-size:12px; letter-spacing:0.16em; color:var(--crust-deep); }
.save-sheet__row { display:flex; align-items:center; justify-content:space-between; }
.save-sheet__toggle-label { font-size:15px; font-weight:600; color:var(--fg1); }
.save-sheet__select { display:flex; flex-direction:column; }
.save-sheet__select-label { font-size:13px; font-weight:600; color:var(--crust-deep); margin-bottom:7px; }
.save-sheet__select-input { border:1.5px solid var(--border-strong); border-radius:8px; padding:12px 14px; background:var(--white); font-family:var(--font-body); font-size:15px; color:var(--fg1); }
.save-sheet__actions { display:flex; gap:12px; margin-top:6px; }
.save-sheet__actions > * { flex:1; }
</style>
