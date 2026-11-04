<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { CalendarOption } from "#shared/calendar";
import { HubError } from "~/composables/useHub";

const emit = defineEmits<{ close: [] }>();

const TABS = [
	{ key: "hub", label: "Verbindung" },
	{ key: "live", label: "Live" },
	{ key: "calendar", label: "Kalender" },
] as const;
type TabKey = (typeof TABS)[number]["key"];
const tab = ref<TabKey>("hub");

// ── Verbindung: per-device hub token ───────────────────────────────────────
const cfg = useRuntimeConfig().public;
const hubSettings = useHubSettings();
const hubUrl = trimUrl(cfg.resonaApiUrl);
const hubTesting = ref(false);
const hubStatus = ref<string | null>(null);

async function testHub(): Promise<void> {
	hubTesting.value = true;
	hubStatus.value = null;
	try {
		const { engines } = await useHub().listEngines();
		hubStatus.value = `Verbunden – ${engines.length} Engines`;
	}
	catch (err) {
		if (err instanceof HubError && err.status === 401) {
			hubStatus.value = "Token ungültig";
		}
		else if (err instanceof HubError && err.status === 0) {
			hubStatus.value = "Hub nicht erreichbar";
		}
		else {
			hubStatus.value = `Fehler: ${err instanceof Error ? err.message : String(err)}`;
		}
	}
	finally {
		hubTesting.value = false;
	}
}

const cal = useCalendar("google");
const settings = cal.settings;

const calendars = ref<CalendarOption[]>([]);
const calendarsLoading = ref(false);

const configured = computed(() => cal.status.value?.configured ?? false);
const connected = computed(() => cal.status.value?.connected ?? false);

async function loadCalendars(): Promise<void> {
	calendarsLoading.value = true;
	try {
		calendars.value = await cal.listCalendars();
	}
	finally {
		calendarsLoading.value = false;
	}
}

// Number inputs hand back strings — clamp into a sane, non-negative range.
function setDaysBack(value: string): void {
	settings.daysBack = Math.max(0, Math.min(365, Math.trunc(Number(value) || 0)));
}

function setDaysAhead(value: string): void {
	settings.daysAhead = Math.max(1, Math.min(365, Math.trunc(Number(value) || 0)));
}

// Settings persist live to localStorage; "Fertig" applies them by reloading so
// the calendar view re-seeds its window/query and reads from the chosen calendar.
function done(): void {
	emit("close");
	if (typeof window !== "undefined") {
		window.location.reload();
	}
}

// Calendars load only once the Kalender tab is first opened.
let calendarsRequested = false;
watch(tab, (value) => {
	if (value === "calendar" && !calendarsRequested) {
		calendarsRequested = true;
		void loadCalendars();
	}
});
</script>

<template>
	<div
		class="drawer"
		data-testid="settings-drawer"
	>
		<div
			class="drawer__scrim"
			data-testid="settings-scrim"
			@click="emit('close')"
		/>
		<aside class="drawer__panel">
			<header class="drawer__head">
				<h2 class="drawer__title">
					Einstellungen
				</h2>
				<button
					class="drawer__close"
					type="button"
					aria-label="Schließen"
					data-testid="settings-close"
					@click="emit('close')"
				>
					✕
				</button>
			</header>

			<nav class="drawer__tabs">
				<button
					v-for="t in TABS"
					:key="t.key"
					class="drawer__tab"
					:class="{ 'drawer__tab--active': tab === t.key }"
					type="button"
					:data-testid="`settings-tab-${t.key}`"
					@click="tab = t.key"
				>
					{{ t.label }}
				</button>
			</nav>
			<section
				v-if="tab === 'hub'"
				class="drawer__body"
			>
				<label class="drawer__field">
					<span class="drawer__label">Hub-Token</span>
					<input
						v-model="hubSettings.token"
						class="drawer__input"
						type="password"
						autocomplete="off"
						placeholder="Token dieses Geräts"
						data-testid="settings-hub-token"
					>
				</label>

				<div class="drawer__field">
					<span class="drawer__label">Hub-URL</span>
					<span
						class="drawer__readonly"
						data-testid="settings-hub-url"
					>{{ hubUrl }}</span>
				</div>

				<p class="drawer__sub">
					Der Token wird nur auf diesem Gerät gespeichert.
				</p>

				<HButton
					variant="secondary"
					:disabled="hubTesting"
					data-testid="settings-hub-test"
					@click="testHub"
				>
					Verbindung testen
				</HButton>

				<p
					v-if="hubStatus"
					class="drawer__hint"
					data-testid="settings-hub-status"
				>
					{{ hubStatus }}
				</p>
			</section>

			<section
				v-else-if="tab === 'calendar'"
				class="drawer__body"
			>
				<!-- Server has no OAuth client configured -->
				<p
					v-if="cal.status.value && !configured"
					class="drawer__notice"
					data-testid="settings-cal-unconfigured"
				>
					Google-Kalender ist auf dem Server nicht konfiguriert.
				</p>

				<!-- Configured but this browser isn't connected yet -->
				<div
					v-else-if="cal.status.value && !connected"
					class="drawer__connect"
					data-testid="settings-cal-connect"
				>
					<p class="drawer__hint">
						Verbinde deinen Google-Kalender, um Kalender und Filter zu wählen.
					</p>
					<a
						class="drawer__connect-btn"
						:href="cal.connectUrl"
						data-testid="settings-cal-connect-link"
					>
						Mit Google verbinden
					</a>
				</div>

				<!-- Connected: calendar selection + filter criteria -->
				<template v-else-if="connected">
					<label class="drawer__field">
						<span class="drawer__label">Kalender</span>
						<select
							v-model="settings.calendarId"
							class="drawer__input"
							data-testid="settings-calendar"
						>
							<option value="">
								Hauptkalender (Standard)
							</option>
							<option
								v-for="c in calendars"
								:key="c.id"
								:value="c.id"
							>
								{{ c.summary }}{{ c.primary ? " ★" : "" }}
							</option>
						</select>
						<span
							v-if="calendarsLoading"
							class="drawer__sub"
						>Lädt Kalender…</span>
					</label>

					<label class="drawer__field">
						<span class="drawer__label">Standard-Suche</span>
						<input
							v-model="settings.query"
							class="drawer__input"
							type="text"
							placeholder="z. B. Visite"
							data-testid="settings-query"
						>
					</label>

					<div class="drawer__row">
						<label class="drawer__field">
							<span class="drawer__label">Tage rückwärts</span>
							<input
								:value="settings.daysBack"
								class="drawer__input"
								type="number"
								min="0"
								max="365"
								inputmode="numeric"
								data-testid="settings-days-back"
								@input="setDaysBack(($event.target as HTMLInputElement).value)"
							>
						</label>
						<label class="drawer__field">
							<span class="drawer__label">Tage vorwärts</span>
							<input
								:value="settings.daysAhead"
								class="drawer__input"
								type="number"
								min="1"
								max="365"
								inputmode="numeric"
								data-testid="settings-days-ahead"
								@input="setDaysAhead(($event.target as HTMLInputElement).value)"
							>
						</label>
					</div>

					<p class="drawer__sub">
						Gilt beim Öffnen der Kalenderseite. Änderungen werden automatisch gespeichert.
					</p>

					<NuxtLink
						class="drawer__link"
						to="/calendar"
						data-testid="settings-open-calendar"
						@click="emit('close')"
					>
						Zur Kalenderseite →
					</NuxtLink>
				</template>

				<p
					v-else
					class="drawer__hint"
				>
					Lädt…
				</p>
			</section>

			<!-- Live settings arrive with the live-sources step. -->
			<section
				v-else
				class="drawer__body"
			/>

			<footer class="drawer__foot">
				<NuxtLink
					class="drawer__link drawer__diag-link"
					to="/debug"
					data-testid="settings-open-debug"
					@click="emit('close')"
				>
					Diagnose (Diktieren) →
				</NuxtLink>
				<HButton
					variant="primary"
					data-testid="settings-done"
					@click="done"
				>
					Fertig
				</HButton>
			</footer>
		</aside>
	</div>
</template>

<style scoped>
.drawer { position:fixed; inset:0; z-index:60; display:flex; justify-content:flex-end; }
.drawer__scrim { position:absolute; inset:0; background:rgba(35,25,17,0.72); }
.drawer__panel { position:relative; width:min(420px, 92vw); height:100%; background:var(--white); display:flex; flex-direction:column; box-shadow:var(--shadow-lift); }
.drawer__head { display:flex; align-items:center; justify-content:space-between; padding:20px 22px 12px; }
.drawer__title { margin:0; font-family:var(--font-display); font-weight:600; font-size:20px; color:var(--fg1); }
.drawer__close { border:none; background:none; font-size:18px; color:var(--fg3); cursor:pointer; padding:6px; line-height:1; }
.drawer__tabs { display:flex; gap:4px; padding:0 22px; border-bottom:1.5px solid var(--border-strong); }
.drawer__tab { border:none; background:none; padding:10px 4px 12px; margin-right:14px; font-size:15px; font-weight:600; color:var(--fg3); cursor:pointer; border-bottom:2px solid transparent; }
.drawer__tab--active { color:var(--accent); border-bottom-color:var(--accent); }
.drawer__body { flex:1; overflow-y:auto; padding:20px 22px; display:flex; flex-direction:column; gap:18px; }
.drawer__notice { margin:0; padding:14px; border-radius:12px; background:var(--butter-soft); color:#8B6200; font-size:14px; line-height:1.5; }
.drawer__connect { display:flex; flex-direction:column; gap:14px; align-items:flex-start; }
.drawer__hint { margin:0; font-size:15px; line-height:1.6; color:var(--fg1); }
.drawer__connect-btn { display:inline-block; background:var(--accent); color:var(--fg-on-accent); font-weight:600; font-size:15px; padding:12px 16px; border-radius:10px; text-decoration:none; }
.drawer__field { display:flex; flex-direction:column; flex:1; }
.drawer__label { font-size:13px; font-weight:600; color:var(--crust-deep); margin-bottom:7px; }
.drawer__input { border:1.5px solid var(--border-strong); border-radius:8px; padding:12px 14px; background:var(--white); font-family:var(--font-body); font-size:15px; color:var(--fg1); }
.drawer__row { display:flex; gap:12px; }
.drawer__sub { font-size:13px; color:var(--fg3); margin:0; }
.drawer__readonly { font-size:15px; color:var(--fg2); word-break:break-all; }
.drawer__link { color:var(--accent); font-weight:600; font-size:15px; text-decoration:none; margin-top:4px; }
.drawer__foot { padding:14px 22px 22px; border-top:1.5px solid var(--border-strong); display:flex; flex-direction:column; gap:10px; }
.drawer__foot > * { width:100%; }
.drawer__diag-link { text-align:center; font-size:13px; }
</style>
