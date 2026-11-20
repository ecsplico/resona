<script setup lang="ts">
import { computed, onMounted } from "vue";
import { dayOf, formatDateTime, formatDayLabel, formatTime, isDay, liveQuery, TERMIN_MARK_LABELS } from "~~/shared/termine";

// `/termin/<event id>?date=YYYY-MM-DD`: the day's list is fetched again and the
// appointment picked from it, so no patient name travels in this URL.
const route = useRoute();
const nav = useBackNav();
const id = String(route.params.id);
const day = computed(() => (isDay(route.query.date) ? route.query.date : dayOf(new Date())));
const homePath = computed(() => (day.value === dayOf(new Date()) ? "/" : `/?date=${day.value}`));

const termine = useTermine(day);
const row = computed(() => termine.rows.value.find(r => r.event.id === id) ?? null);
const marks = useTerminMarks([termine.view]);

function startDictation(): void {
	if (!row.value) return;
	void navigateTo({ path: "/live", query: liveQuery(row.value.event) });
}

onMounted(() => {
	void termine.refresh();
});
</script>

<template>
	<section class="termin">
		<button
			type="button"
			class="termin__back"
			data-testid="termin-back"
			@click="nav.back(homePath)"
		>
			‹ {{ formatDayLabel(day) }}
		</button>

		<p
			v-if="termine.error.value"
			class="termin__error"
			role="alert"
		>
			{{ termine.error.value }}
		</p>

		<p
			v-if="termine.loading.value && !row"
			class="termin__hint"
		>
			Termin wird geladen…
		</p>

		<p
			v-else-if="!row && termine.view.value"
			class="termin__hint"
			data-testid="termin-missing"
		>
			Dieser Termin ist an diesem Tag nicht (mehr) im Kalender.
		</p>

		<template v-if="row">
			<header class="termin__head">
				<div class="termin__head-text">
					<span class="termin__time">{{ formatTime(row.event) }}</span>
					<h1
						class="termin__title"
						data-testid="termin-title"
					>
						{{ row.event.title }}
					</h1>
					<span class="termin__status">
						<TerminStatusChip
							:dictations="row.dictations.length"
							:mark="row.event.mark"
						/>
					</span>
				</div>
				<TerminActions
					:event="row.event"
					:dictate="false"
					@mark="marks.mark(row.event, $event)"
					@unmark="marks.unmark(row.event)"
				/>
			</header>

			<p
				v-if="marks.lastMark.value"
				class="termin__notice"
				role="status"
				data-testid="mark-undo-notice"
			>
				<span>„{{ TERMIN_MARK_LABELS[marks.lastMark.value.status] }}“ vermerkt.</span>
				<button
					class="termin__undo"
					type="button"
					data-testid="mark-undo"
					@click="marks.undo()"
				>
					Rückgängig
				</button>
			</p>
			<p
				v-if="marks.error.value"
				class="termin__error"
				role="alert"
				data-testid="mark-error"
			>
				{{ marks.error.value }}
			</p>

			<HButton
				class="termin__start"
				type="button"
				data-testid="termin-start"
				@click="startDictation"
			>
				Diktat starten
			</HButton>

			<h2 class="termin__section">
				Diktate
			</h2>
			<p
				v-if="row.dictations.length === 0"
				class="termin__hint"
				data-testid="termin-no-dictations"
			>
				Noch kein Diktat zu diesem Termin.
			</p>
			<ul
				v-else
				class="h-list termin__list"
				data-testid="termin-dictations"
			>
				<li
					v-for="recording in row.dictations"
					:key="recording.id"
				>
					<NuxtLink
						class="dictation-row"
						:to="{ name: 'recording-id', params: { id: recording.id } }"
						data-testid="termin-dictation"
					>
						<span class="dictation-row__title">{{ recording.title }}</span>
						<span class="dictation-row__meta">{{ formatDateTime(recording.date_created) }}</span>
						<span class="dictation-row__chips">
							<HChip
								v-if="recording.adopted_at"
								tone="success"
							>
								Übernommen
							</HChip>
							<StatusChip
								v-if="recording.status !== 'done'"
								:status="recording.status"
							/>
						</span>
					</NuxtLink>
				</li>
			</ul>
		</template>
	</section>
</template>

<style scoped>
.termin { width:100%; max-width:720px; margin:0 auto; display:flex; flex-direction:column; gap:12px; padding:12px 16px 32px; }
.termin__back { align-self:flex-start; border:none; background:none; padding:6px 0; color:var(--accent-ink); font:inherit; font-size:var(--fs-sm); font-weight:600; cursor:pointer; }
.termin__error { margin:0; color:var(--danger); font-size:var(--fs-sm); }
.termin__hint { margin:0; color:var(--fg3); font-size:var(--fs-md); }
.termin__head { display:flex; align-items:flex-start; gap:8px; }
.termin__head-text { flex:1; min-width:0; display:flex; flex-direction:column; gap:4px; }
.termin__status { display:flex; }
.termin__notice { margin:0; display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:8px; padding:10px 14px; border-radius:var(--r-md); background:var(--olive-soft); color:var(--fg1); font-size:var(--fs-sm); }
.termin__undo { border:none; background:none; padding:4px 0; color:var(--accent-ink); font:inherit; font-weight:600; cursor:pointer; }
.termin__time { font-family:var(--font-mono); font-variant-numeric:tabular-nums; font-size:var(--fs-sm); color:var(--fg2); }
.termin__title { margin:0; font-family:var(--font-display); font-size:var(--fs-xl); font-weight:600; color:var(--fg1); overflow-wrap:anywhere; }
.termin__start { align-self:stretch; }
.termin__section { margin:12px 0 0; font-size:var(--fs-sm); font-weight:600; letter-spacing:.04em; text-transform:uppercase; color:var(--fg3); }
.termin__list { margin:0; padding:0; list-style:none; }
.dictation-row { display:grid; grid-template-columns:1fr auto; grid-template-areas:"title chips" "meta chips"; align-items:center; column-gap:12px; row-gap:2px; padding:12px 16px; color:var(--fg1); text-decoration:none; }
.dictation-row:hover { background:var(--row-hover); }
.dictation-row__title { grid-area:title; font-weight:500; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.dictation-row__meta { grid-area:meta; font-size:var(--fs-xs); color:var(--fg3); }
.dictation-row__chips { grid-area:chips; display:flex; gap:6px; }
@media (min-width:960px) {
	.termin__start { align-self:flex-start; }
}
</style>
