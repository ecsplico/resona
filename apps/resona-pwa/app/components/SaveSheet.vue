<script setup lang="ts">
import { onMounted, ref } from "vue";
import type { Ref } from "vue";

withDefaults(
	defineProps<{ initialTitle?: string }>(),
	{ initialTitle: "" },
);

const emit = defineEmits<{
	save: [{ title: string; language: string; profile: string; transcribe: boolean }];
	discard: [];
}>();

const cfg = useRuntimeConfig().public;

const title = ref("");
const language = ref(cfg.defaultLanguage);
const profile = ref(cfg.defaultProfile);
const transcribe = ref(true);

const profileOptions: Ref<string[]> = ref([cfg.defaultProfile]);
const languageOptions = ["de", "en"];

const { list } = useProfiles();

onMounted(async () => {
	const names = await list();
	if (names.length > 0) {
		profileOptions.value = names;
		if (!names.includes(profile.value)) {
			profile.value = names[0]!;
		}
	}
});

function submit(): void {
	if (!title.value.trim()) {
		return;
	}
	emit("save", {
		title: title.value.trim(),
		language: language.value,
		profile: profile.value,
		transcribe: transcribe.value,
	});
}
</script>

<template>
	<div class="save-sheet">
		<div class="save-sheet__scrim" />
		<div class="save-sheet__sheet">
			<p class="save-sheet__eyebrow">
				AUFNAHME SPEICHERN
			</p>

			<HField
				v-model="title"
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
					:disabled="!title.trim()"
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
.save-sheet__sheet { position:relative; background:var(--white); border-radius:var(--r-lg) var(--r-lg) 0 0; padding:28px 24px 36px; display:flex; flex-direction:column; gap:18px; }
.save-sheet__eyebrow { margin:0; font-family:var(--font-body); font-weight:600; font-size:12px; letter-spacing:0.16em; color:var(--crust-deep); }
.save-sheet__row { display:flex; align-items:center; justify-content:space-between; }
.save-sheet__toggle-label { font-size:15px; font-weight:600; color:var(--fg1); }
.save-sheet__select { display:flex; flex-direction:column; }
.save-sheet__select-label { font-size:13px; font-weight:600; color:var(--crust-deep); margin-bottom:7px; }
.save-sheet__select-input { border:1.5px solid var(--border-strong); border-radius:8px; padding:12px 14px; background:var(--white); font-family:var(--font-body); font-size:15px; color:var(--fg1); }
.save-sheet__actions { display:flex; gap:12px; margin-top:6px; }
.save-sheet__actions > * { flex:1; }
</style>
