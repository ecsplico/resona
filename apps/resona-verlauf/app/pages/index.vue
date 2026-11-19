<script setup lang="ts">
import { computed, onMounted } from "vue";
import { dayOf, formatDayLabel, formatTime, isDay, shiftDay } from "~~/shared/termine";

type Tab = "termine" | "diktat";

const route = useRoute();
const router = useRouter();

const todayDay = dayOf(new Date());
const day = computed(() => (isDay(route.query.date) ? route.query.date : todayDay));
const tab = computed<Tab>(() => (route.query.tab === "diktat" ? "diktat" : "termine"));
const isToday = computed(() => day.value === todayDay);
const dayLabel = computed(() => formatDayLabel(day.value));

const termine = useTermine(day);

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

function openTermin(id: string): void {
	void navigateTo({ name: "termin-id", params: { id }, query: { date: day.value } });
}

async function connect(): Promise<void> {
	window.location.assign(await termine.connectUrl());
}

onMounted(() => {
	void termine.refresh();
});
</script>

<template>
	<section class="home">
		<div class="home__day">
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
				>
					<button
						class="termin-row"
						type="button"
						data-testid="termin-row"
						@click="openTermin(row.event.id)"
					>
						<span class="termin-row__time">{{ formatTime(row.event) }}</span>
						<span class="termin-row__title">{{ row.event.title }}</span>
						<HChip
							v-if="row.dictations.length > 0"
							tone="success"
							dot
							data-testid="termin-status-done"
						>
							Diktiert{{ row.dictations.length > 1 ? ` (${row.dictations.length})` : "" }}
						</HChip>
						<HChip
							v-else
							tone="neutral"
							data-testid="termin-status-open"
						>
							Offen
						</HChip>
					</button>
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
.termin-row { width:100%; display:grid; grid-template-columns:auto 1fr auto; align-items:center; gap:12px; padding:14px 16px; border:none; background:none; text-align:left; font:inherit; color:var(--fg1); cursor:pointer; }
.termin-row:hover { background:var(--row-hover); }
.termin-row:active { background:var(--row-active); }
.termin-row__time { font-family:var(--font-mono); font-variant-numeric:tabular-nums; font-size:var(--fs-sm); color:var(--fg2); min-width:5ch; }
.termin-row__title { font-size:var(--fs-md); font-weight:500; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.home__free { padding:0 16px; }
.home__free-button { width:100%; }
@media (min-width:960px) {
	.home__free-button { width:auto; }
}
</style>
