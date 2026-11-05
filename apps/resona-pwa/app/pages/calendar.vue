<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { eventDisplayTitle, formatEventWhen, germanDateToIso, isoDateToGerman } from "#shared/calendar";
import type { CalendarEvent } from "#shared/calendar";
import { isCalendarEnabled } from "#shared/calendarEnabled";

// Off by default (NUXT_PUBLIC_CALENDAR_ENABLED): 404 before any calendar state exists.
definePageMeta({
	middleware: () => {
		if (!isCalendarEnabled(useRuntimeConfig().public.calendarEnabled)) {
			return abortNavigation(createError({ statusCode: 404, statusMessage: "Seite nicht gefunden", fatal: true }));
		}
	},
});

const cal = useCalendar("google");
const showSettings = ref(false);

const connected = computed(() => cal.status.value?.connected ?? false);
const configured = computed(() => cal.status.value?.configured ?? false);

// German-formatted (TT.MM.JJJJ) mirrors of the ISO filter dates. Bound to text
// inputs and committed on blur/change so typing isn't snapped back mid-edit.
const fromText = ref(isoDateToGerman(cal.filter.from));
const toText = ref(isoDateToGerman(cal.filter.to));

function commitFrom(): void {
	const iso = germanDateToIso(fromText.value);
	if (iso) {
		cal.filter.from = iso;
	}
	fromText.value = isoDateToGerman(cal.filter.from); // normalize / revert invalid
	void cal.refresh();
}

function commitTo(): void {
	const iso = germanDateToIso(toText.value);
	if (iso) {
		cal.filter.to = iso;
	}
	toText.value = isoDateToGerman(cal.filter.to);
	void cal.refresh();
}

function startFromEvent(event: CalendarEvent): void {
	// Hand the event's "<name> – <date>" title to the live page, which pre-fills
	// it as the recording name on save.
	void navigateTo({ path: "/live", query: { title: eventDisplayTitle(event) } });
}

onMounted(async () => {
	await cal.refresh();
});
</script>

<template>
	<div class="cal">
		<HAppBar
			tone="light"
			title="Kalender"
		>
			<template #leading>
				<NuxtLink
					class="cal__back"
					to="/"
				>
					‹ Zurück
				</NuxtLink>
			</template>
			<template #trailing>
				<HChip
					v-if="connected"
					tone="success"
					dot
					data-testid="cal-connected"
				>
					Verbunden
				</HChip>
				<button
					class="cal__cog"
					type="button"
					aria-label="Einstellungen"
					data-testid="nav-settings"
					@click="showSettings = true"
				>
					<svg
						width="22"
						height="22"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						stroke-linejoin="round"
						aria-hidden="true"
					>
						<circle
							cx="12"
							cy="12"
							r="3"
						/>
						<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
					</svg>
				</button>
			</template>
		</HAppBar>

		<!-- Not configured on the server -->
		<div
			v-if="cal.status.value && !configured"
			class="cal__notice"
			data-testid="cal-unconfigured"
		>
			Google-Kalender ist auf dem Server nicht konfiguriert
			(<code>NUXT_GOOGLE_CLIENT_ID</code> / <code>…SECRET</code> /
			<code>NUXT_SESSION_PASSWORD</code>).
		</div>

		<!-- Configured but not connected -->
		<div
			v-else-if="cal.status.value && !connected"
			class="cal__connect"
			data-testid="cal-connect"
		>
			<p class="cal__connect-text">
				Verbinde deinen Google-Kalender, um ein Diktat direkt aus einem Termin
				zu starten — der Titel wird automatisch übernommen.
			</p>
			<a
				class="cal__connect-btn"
				:href="cal.connectUrl"
				data-testid="cal-connect-link"
			>
				Mit Google verbinden
			</a>
		</div>

		<!-- Connected: filter + event list -->
		<template v-else-if="connected">
			<div class="cal__filters">
				<label class="cal__date">
					<span class="cal__date-label">Von</span>
					<input
						v-model="fromText"
						class="cal__date-input"
						type="text"
						inputmode="numeric"
						placeholder="TT.MM.JJJJ"
						data-testid="cal-from"
						@change="commitFrom"
						@blur="commitFrom"
					>
				</label>
				<label class="cal__date">
					<span class="cal__date-label">Bis</span>
					<input
						v-model="toText"
						class="cal__date-input"
						type="text"
						inputmode="numeric"
						placeholder="TT.MM.JJJJ"
						data-testid="cal-to"
						@change="commitTo"
						@blur="commitTo"
					>
				</label>
			</div>
			<div class="cal__filters">
				<HField
					v-model="cal.filter.query"
					class="cal__search"
					label="Suchen"
					data-testid="cal-search"
				/>
				<HButton
					class="cal__refresh"
					variant="secondary"
					data-testid="cal-refresh"
					@click="cal.refresh()"
				>
					Aktualisieren
				</HButton>
			</div>

			<p
				v-if="cal.error.value"
				class="cal__error"
				data-testid="cal-error"
			>
				{{ cal.error.value }}
			</p>

			<div class="cal__list">
				<p
					v-if="cal.loading.value"
					class="cal__hint"
				>
					Lädt…
				</p>
				<p
					v-else-if="cal.events.value.length === 0"
					class="cal__hint"
					data-testid="cal-empty"
				>
					Keine Termine im Zeitraum
				</p>

				<HCard
					v-for="event in cal.events.value"
					:key="event.id"
					class="cal__event"
					data-testid="cal-event"
				>
					<div class="cal__event-info">
						<p class="cal__event-title">
							{{ event.title }}
						</p>
						<p class="cal__event-when">
							{{ formatEventWhen(event) }}
						</p>
					</div>
					<button
						class="cal__event-btn"
						type="button"
						data-testid="cal-transcribe"
						aria-label="Transkribieren"
						title="Transkribieren"
						@click="startFromEvent(event)"
					>
						<svg
							width="22"
							height="22"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="2"
							stroke-linecap="round"
							stroke-linejoin="round"
							aria-hidden="true"
						>
							<rect
								x="9"
								y="2"
								width="6"
								height="12"
								rx="3"
							/>
							<path d="M5 10a7 7 0 0 0 14 0" />
							<line
								x1="12"
								y1="17"
								x2="12"
								y2="22"
							/>
						</svg>
					</button>
				</HCard>
			</div>

			<button
				class="cal__disconnect"
				type="button"
				data-testid="cal-disconnect"
				@click="cal.disconnect()"
			>
				Verbindung trennen<span v-if="cal.status.value?.email"> ({{ cal.status.value.email }})</span>
			</button>
		</template>

		<p
			v-else
			class="cal__hint"
		>
			Lädt…
		</p>

		<SettingsDrawer
			v-if="showSettings"
			@close="showSettings = false"
		/>
	</div>
</template>

<style scoped>
.cal { min-height:100vh; background:var(--bg); padding-bottom:40px; }
.cal__back { color:var(--accent); font-weight:600; font-size:15px; text-decoration:none; }
.cal__cog { border:none; background:none; color:var(--accent); cursor:pointer; display:inline-flex; align-items:center; padding:2px; }
.cal__notice { margin:20px; padding:16px; border-radius:12px; background:var(--butter-soft); color:#8B6200; font-size:14px; line-height:1.5; }
.cal__notice code { font-family:var(--font-mono); font-size:12px; }
.cal__connect { margin:24px 20px; display:flex; flex-direction:column; gap:16px; align-items:flex-start; }
.cal__connect-text { margin:0; font-size:15px; line-height:1.6; color:var(--fg1); }
.cal__connect-btn { display:inline-block; background:var(--accent); color:var(--fg-on-accent); font-weight:600; font-size:15px; padding:14px 18px; border-radius:10px; text-decoration:none; }
.cal__filters { display:flex; gap:12px; align-items:flex-end; padding:16px 20px 4px; }
.cal__date { display:flex; flex-direction:column; flex:1; }
.cal__date-label { font-size:13px; font-weight:600; color:var(--crust-deep); margin-bottom:7px; }
.cal__date-input { border:1.5px solid var(--border-strong); border-radius:8px; padding:12px 14px; background:var(--white); font-family:var(--font-body); font-size:15px; color:var(--fg1); }
.cal__search { flex:1; }
.cal__refresh { white-space:nowrap; }
.cal__error { color:var(--danger); font-size:14px; margin:8px 20px 0; }
.cal__list { display:flex; flex-direction:column; gap:12px; padding:12px 20px 20px; }
.cal__hint { color:var(--fg3); font-size:15px; margin:0; text-align:center; padding:32px 0; }
.cal__event { display:flex; align-items:center; gap:12px; }
.cal__event-info { flex:1; min-width:0; }
.cal__event-title { margin:0 0 4px; font-weight:600; font-size:16px; color:var(--fg1); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.cal__event-when { margin:0; font-size:13px; color:var(--fg3); }
.cal__event-btn { flex-shrink:0; width:46px; height:46px; border-radius:50%; border:none; background:var(--accent); color:var(--white); display:inline-flex; align-items:center; justify-content:center; cursor:pointer; box-shadow:var(--shadow-lift); }
.cal__event-btn:active { transform:scale(0.96); }
.cal__disconnect { display:block; margin:8px auto 0; background:none; border:none; color:var(--fg3); font-size:14px; text-decoration:underline; cursor:pointer; }
</style>
