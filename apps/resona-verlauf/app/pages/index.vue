<script setup lang="ts">
import { computed, onMounted } from "vue";
import type { TerminEvent } from "~~/shared/termine";
import { dayOf, formatDayLabel, formatTerminWhen, formatTime, isDay, isOffenWindow, OFFEN_WINDOWS, shiftDay, TERMIN_MARK_LABELS, terminDay } from "~~/shared/termine";

type Tab = "termine" | "offen" | "diktat";

const route = useRoute();
const router = useRouter();

const todayDay = dayOf(new Date());
const day = computed(() => (isDay(route.query.date) ? route.query.date : todayDay));
const tab = computed<Tab>(() => (route.query.tab === "diktat" || route.query.tab === "offen" ? route.query.tab : "termine"));
const isToday = computed(() => day.value === todayDay);
const dayLabel = computed(() => formatDayLabel(day.value));
// `?zeitraum=3m|6m` (default 4 Wochen) of the "Offen" tab.
const offenWindow = computed(() => (isOffenWindow(route.query.zeitraum) ? route.query.zeitraum : "4w"));

const termine = useTermine(day);
const offen = useOffeneTermine(offenWindow);
// Marks patch both lists at once, so the Offen count and the day's chips stay in step.
const marks = useTerminMarks([termine.view, offen.view]);

// Notice after the Google consent round trip (`/api/termine` redirects to `/?kalender=…`).
const KALENDER_NOTICES: Record<string, string> = {
	verbunden: "Google-Kalender ist verbunden.",
	abgebrochen: "Verbinden abgebrochen.",
	fehler: "Verbinden fehlgeschlagen. Bitte erneut versuchen.",
};
const kalenderNotice = computed(() => {
	const value = route.query.kalender;
	return typeof value === "string" ? KALENDER_NOTICES[value] ?? null : null;
});

function setQuery(patch: Record<string, string | undefined>): void {
	void router.replace({ query: { ...route.query, kalender: undefined, ...patch } });
}

function goToDay(next: string): void {
	setQuery({ date: next === todayDay ? undefined : next });
}

function selectTab(next: Tab): void {
	setQuery({ tab: next === "termine" ? undefined : next });
}

function selectWindow(value: string): void {
	setQuery({ zeitraum: isOffenWindow(value) && value !== "4w" ? value : undefined });
}

// Same target from both lists: the appointment's detail page on its own day.
function openTermin(event: TerminEvent): void {
	void navigateTo({ name: "termin-id", params: { id: event.id }, query: { date: terminDay(event) } });
}

async function connect(): Promise<void> {
	window.location.assign(await termine.connectUrl());
}

onMounted(() => {
	void termine.refresh();
	// Also on the other tabs: the Offen tab's count badge.
	void offen.refresh();
});
</script>

<template>
	<section class="home">
		<div
			v-if="tab !== 'offen'"
			class="home__day"
		>
			<button
				class="home__day-step"
				type="button"
				aria-label="Vorheriger Tag"
				data-testid="day-prev"
				@click="goToDay(shiftDay(day, -1))"
			>
				‹
			</button>
			<div class="home__day-center">
				<h1
					class="home__day-label"
					data-testid="day-label"
				>
					{{ dayLabel }}
				</h1>
				<button
					v-if="!isToday"
					class="home__today"
					type="button"
					data-testid="day-today"
					@click="goToDay(todayDay)"
				>
					Heute
				</button>
			</div>
			<button
				class="home__day-step"
				type="button"
				aria-label="Nächster Tag"
				data-testid="day-next"
				@click="goToDay(shiftDay(day, 1))"
			>
				›
			</button>
		</div>

		<div
			class="h-tabs home__tabs"
			role="tablist"
			aria-label="Ansicht"
		>
			<button
				class="h-tab"
				type="button"
				role="tab"
				:aria-selected="tab === 'termine'"
				data-testid="tab-termine"
				@click="selectTab('termine')"
			>
				Termine
			</button>
			<button
				class="h-tab home__tab"
				type="button"
				role="tab"
				:aria-selected="tab === 'offen'"
				data-testid="tab-offen"
				@click="selectTab('offen')"
			>
				Offen
				<span
					v-if="offen.view.value?.connected && offen.events.value.length > 0"
					class="home__badge"
					data-testid="tab-offen-count"
				>{{ offen.events.value.length }}</span>
			</button>
			<button
				class="h-tab"
				type="button"
				role="tab"
				:aria-selected="tab === 'diktat'"
				data-testid="tab-diktat"
				@click="selectTab('diktat')"
			>
				Diktat
			</button>
		</div>

		<p
			v-if="kalenderNotice"
			class="home__notice"
			role="status"
			data-testid="kalender-notice"
		>
			{{ kalenderNotice }}
		</p>

		<p
			v-if="marks.lastMark.value && tab !== 'diktat'"
			class="home__notice home__undo"
			role="status"
			data-testid="mark-undo-notice"
		>
			<span>„{{ TERMIN_MARK_LABELS[marks.lastMark.value.status] }}“ vermerkt: {{ formatTerminWhen(marks.lastMark.value.event) }}</span>
			<button
				class="home__undo-button"
				type="button"
				data-testid="mark-undo"
				@click="marks.undo()"
			>
				Rückgängig
			</button>
		</p>

		<p
			v-if="marks.error.value && tab !== 'diktat'"
			class="home__error"
			role="alert"
			data-testid="mark-error"
		>
			{{ marks.error.value }}
		</p>

		<div
			v-if="tab === 'termine'"
			class="home__panel"
			role="tabpanel"
			data-testid="panel-termine"
		>
			<p
				v-if="termine.error.value"
				class="home__error"
				role="alert"
				data-testid="termine-error"
			>
				{{ termine.error.value }}
			</p>

			<HCard
				v-if="termine.view.value && !termine.connected.value"
				class="home__connect"
				data-testid="termine-connect"
			>
				<h2 class="home__connect-title">
					Kalender nicht verbunden
				</h2>
				<p class="home__connect-text">
					Einmal mit dem Google-Konto der Praxis verbinden, dann erscheinen hier die EG- und FG-Termine des Tages.
				</p>
				<HButton
					type="button"
					data-testid="termine-connect-button"
					@click="connect"
				>
					Google-Kalender verbinden
				</HButton>
			</HCard>

			<p
				v-else-if="termine.loading.value && !termine.view.value"
				class="home__hint"
				data-testid="termine-loading"
			>
				Termine werden geladen…
			</p>

			<p
				v-else-if="termine.connected.value && termine.rows.value.length === 0 && !termine.error.value"
				class="home__hint"
				data-testid="termine-empty"
			>
				Keine EG- oder FG-Termine an diesem Tag.
			</p>

			<ul
				v-if="termine.rows.value.length > 0"
				class="h-list home__list"
				data-testid="termine-list"
			>
				<li
					v-for="row in termine.rows.value"
					:key="row.event.id"
					class="termin-item"
				>
					<button
						class="termin-row"
						type="button"
						data-testid="termin-row"
						@click="openTermin(row.event)"
					>
						<span class="termin-row__time">{{ formatTime(row.event) }}</span>
						<span class="termin-row__title">{{ row.event.title }}</span>
						<TerminStatusChip
							:dictations="row.dictations.length"
							:mark="row.event.mark"
						/>
					</button>
					<TerminActions
						:event="row.event"
						@mark="marks.mark(row.event, $event)"
						@unmark="marks.unmark(row.event)"
					/>
				</li>
			</ul>
		</div>

		<div
			v-else-if="tab === 'offen'"
			class="home__panel"
			role="tabpanel"
			data-testid="panel-offen"
		>
			<div class="offen__bar">
				<label class="offen__window">
					<span class="offen__window-label">Zeitraum</span>
					<select
						class="offen__window-input"
						:value="offenWindow"
						data-testid="offen-window"
						@change="selectWindow(($event.target as HTMLSelectElement).value)"
					>
						<option
							v-for="option in OFFEN_WINDOWS"
							:key="option.value"
							:value="option.value"
						>
							{{ option.label }}
						</option>
					</select>
				</label>
			</div>

			<p
				v-if="offen.error.value"
				class="home__error"
				role="alert"
				data-testid="offen-error"
			>
				{{ offen.error.value }}
			</p>

			<p
				v-if="offen.view.value && !offen.connected.value"
				class="home__hint"
				data-testid="offen-not-connected"
			>
				Kalender nicht verbunden. Im Reiter „Termine“ verbinden.
			</p>

			<p
				v-else-if="offen.loading.value && !offen.view.value"
				class="home__hint"
				data-testid="offen-loading"
			>
				Offene Termine werden geladen…
			</p>

			<p
				v-else-if="offen.connected.value && offen.events.value.length === 0 && !offen.error.value"
				class="home__hint"
				data-testid="offen-empty"
			>
				Keine offenen Termindiktate in diesem Zeitraum.
			</p>

			<ul
				v-if="offen.events.value.length > 0"
				class="h-list home__list"
				data-testid="offen-list"
			>
				<li
					v-for="event in offen.events.value"
					:key="event.id"
					class="termin-item"
				>
					<button
						class="termin-row offen-row"
						type="button"
						data-testid="offen-row"
						@click="openTermin(event)"
					>
						<span class="offen-row__when">{{ formatTerminWhen(event) }}</span>
						<span class="termin-row__title">{{ event.title }}</span>
					</button>
					<TerminActions
						:event="event"
						@mark="marks.mark(event, $event)"
						@unmark="marks.unmark(event)"
					/>
				</li>
			</ul>
		</div>

		<div
			v-else
			class="home__panel"
			role="tabpanel"
			data-testid="panel-diktat"
		>
			<div class="home__free">
				<HButton
					class="home__free-button"
					type="button"
					data-testid="diktat-ohne-termin"
					@click="navigateTo('/live')"
				>
					Diktat ohne Termin
				</HButton>
			</div>
			<RecordingsPane />
		</div>
	</section>
</template>

<style scoped>
.home { width:100%; max-width:960px; margin:0 auto; display:flex; flex-direction:column; }
.home__day { display:grid; grid-template-columns:44px 1fr 44px; align-items:center; gap:8px; padding:12px 16px 4px; }
.home__day-center { display:flex; flex-direction:column; align-items:center; gap:4px; min-width:0; }
.home__day-label { margin:0; font-family:var(--font-display); font-size:var(--fs-lg); font-weight:600; color:var(--fg1); text-align:center; }
.home__day-step { width:44px; height:44px; border:none; border-radius:var(--r-full); background:none; color:var(--fg2); font-size:var(--fs-2xl); line-height:1; cursor:pointer; }
.home__day-step:hover { background:var(--cream-deep); color:var(--fg1); }
.home__today { border:1px solid var(--border-strong); border-radius:var(--r-full); background:var(--bg-raised); color:var(--accent-ink); font-size:var(--fs-xs); font-weight:600; padding:2px 12px; cursor:pointer; }
.home__tabs { padding:0 16px; }
.home__notice { margin:12px 16px 0; padding:10px 14px; border-radius:var(--r-md); background:var(--olive-soft); color:var(--fg1); font-size:var(--fs-sm); }
.home__panel { display:flex; flex-direction:column; gap:12px; padding:16px 0 0; }
.home__error { margin:0 16px; color:var(--danger); font-size:var(--fs-sm); }
.home__hint { margin:0 16px; color:var(--fg3); font-size:var(--fs-md); }
.home__connect { margin:0 16px; display:flex; flex-direction:column; gap:12px; align-items:flex-start; }
.home__connect-title { margin:0; font-family:var(--font-display); font-size:var(--fs-lg); font-weight:600; color:var(--fg1); }
.home__connect-text { margin:0; color:var(--fg2); font-size:var(--fs-sm); line-height:1.5; }
.home__list { margin:0 16px; padding:0; list-style:none; }
.termin-item { display:flex; align-items:center; gap:4px; padding-right:8px; }
.termin-row { flex:1; min-width:0; display:grid; grid-template-columns:auto 1fr auto; align-items:center; gap:12px; padding:14px 16px; border:none; background:none; text-align:left; font:inherit; color:var(--fg1); cursor:pointer; }
.termin-row:hover { background:var(--row-hover); }
.termin-row:active { background:var(--row-active); }
.termin-row__time { font-family:var(--font-mono); font-variant-numeric:tabular-nums; font-size:var(--fs-sm); color:var(--fg2); min-width:5ch; }
.termin-row__title { font-size:var(--fs-md); font-weight:500; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.home__tab { display:inline-flex; align-items:center; gap:6px; }
.home__badge { min-width:20px; padding:1px 6px; border-radius:var(--r-full); background:var(--accent); color:var(--white); font-size:var(--fs-xs); font-weight:600; line-height:1.4; text-align:center; }
.offen__bar { display:flex; justify-content:flex-end; padding:0 16px; }
.offen__window { display:flex; align-items:center; gap:8px; }
.offen__window-label { font-size:var(--fs-sm); font-weight:600; color:var(--fg2); }
.offen__window-input { border:1.5px solid var(--border-strong); border-radius:var(--r-md); padding:8px 12px; background:var(--bg-raised); font-family:var(--font-body); font-size:var(--fs-sm); color:var(--fg1); }
.home__undo { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:8px; }
.home__undo-button { border:none; background:none; padding:4px 0; color:var(--accent-ink); font:inherit; font-weight:600; cursor:pointer; }
.offen-row { grid-template-columns:auto 1fr; }
.offen-row__when { font-family:var(--font-mono); font-variant-numeric:tabular-nums; font-size:var(--fs-sm); color:var(--fg2); white-space:nowrap; }
.home__free { padding:0 16px; }
.home__free-button { width:100%; }
@media (min-width:960px) {
	.home__free-button { width:auto; }
}
</style>
