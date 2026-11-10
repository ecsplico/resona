<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useEventListener } from "@vueuse/core";
import type { CalendarOption } from "#shared/calendar";
import { isCalendarEnabled } from "#shared/calendarEnabled";
import { HubError, type HubEngine, type ListenEngine } from "~/composables/useHub";
import { CHUNK_SECONDS_MAX, CHUNK_SECONDS_MIN, clampChunkSeconds } from "~/composables/useLiveSettings";
import { DEVICE_SPEECH_HINTS, type DeviceSpeechAvailability } from "~/composables/useDeviceSpeech";

const emit = defineEmits<{ close: [] }>();

const ALL_TABS = [
	{ key: "hub", label: "Verbindung" },
	{ key: "live", label: "Live" },
	{ key: "calendar", label: "Kalender" },
] as const;
type TabKey = (typeof ALL_TABS)[number]["key"];
const calendarEnabled = isCalendarEnabled(useRuntimeConfig().public.calendarEnabled);
const TABS = ALL_TABS.filter(t => t.key !== "calendar" || calendarEnabled);
const tab = ref<TabKey>("hub");

// ── Verbindung: per-device hub token ───────────────────────────────────────
const cfg = useRuntimeConfig().public;
const hubSettings = useHubSettings();
const hubUrl = trimUrl(cfg.resonaApiUrl);
const hubTesting = ref(false);
const hubStatus = ref<string | null>(null);

// Directus mode: the token is also kept on the user's account, so another
// device of the same user picks it up at login instead of asking again.
const tokenSync = useHubTokenSync();
const accountSync = ref<string | null>(null);

async function saveTokenToAccount(): Promise<void> {
	if (!tokenSync.enabled()) {
		return;
	}
	accountSync.value = null;
	try {
		await tokenSync.push(hubSettings.token);
		accountSync.value = hubSettings.token.trim()
			? "Token im Konto gespeichert – auf deinen anderen Geräten wird er automatisch übernommen."
			: "Token aus dem Konto entfernt.";
	}
	catch {
		accountSync.value = "Token konnte nicht im Konto gespeichert werden – er gilt nur auf diesem Gerät.";
	}
}

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

// ── Live: source, hub listen engine, on-device speech ──────────────────────
const liveSettings = useLiveSettings();
const deviceSpeech = useDeviceSpeech();
const listenEngines = ref<ListenEngine[]>([]);
const listenDefault = ref<string | null>(null);
const listenLoaded = ref(false);
const listenError = ref<string | null>(null);
const deviceState = ref<DeviceSpeechAvailability | null>(null);
const deviceInstalling = ref(false);

const DEVICE_LABELS: Record<DeviceSpeechAvailability, string> = {
	available: "Bereit (lokal)",
	downloadable: "Sprachpaket verfügbar",
	downloading: "Wird geladen…",
	unavailable: DEVICE_SPEECH_HINTS.unavailable,
	unsupported: DEVICE_SPEECH_HINTS.unsupported,
};
const deviceLabel = computed(() => deviceState.value ? DEVICE_LABELS[deviceState.value] : "Wird geprüft…");

async function loadListenEngines(): Promise<void> {
	listenError.value = null;
	try {
		const list = await useHub().listListenEngines();
		listenEngines.value = list.engines;
		listenDefault.value = list.default;
	}
	catch (err) {
		listenEngines.value = [];
		listenError.value = err instanceof Error ? err.message : String(err);
	}
	finally {
		listenLoaded.value = true;
	}
}

async function checkDevice(): Promise<void> {
	try {
		deviceState.value = await deviceSpeech.availability(cfg.defaultLanguage);
	}
	catch {
		deviceState.value = "unavailable";
	}
}

async function installDevice(): Promise<void> {
	deviceInstalling.value = true;
	try {
		await deviceSpeech.install(cfg.defaultLanguage);
	}
	catch {
		// Re-check below reports the resulting state.
	}
	finally {
		deviceInstalling.value = false;
	}
	await checkDevice();
}

// ── Live correction: better hub model re-transcribes chunks ────────────────
const correctionEngines = ref<HubEngine[]>([]);

async function loadCorrectionEngines(): Promise<void> {
	try {
		const { engines } = await useHub().listEngines();
		correctionEngines.value = engines.filter(e => e.job_kind === "audio" && e.available);
	}
	catch {
		// Unreachable hub: only "Hub-Standard" stays selectable.
		correctionEngines.value = [];
	}
}

// Clamp on change (not input) so typing "20" doesn't clamp the interim "2".
function setChunkSeconds(value: string): void {
	liveSettings.chunkSeconds = clampChunkSeconds(Number(value));
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

// ── Modal behaviour: focus moves in on open and back to the opener on close ──
const closeButton = ref<HTMLButtonElement | null>(null);
let opener: HTMLElement | null = null;

onMounted(() => {
	opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
	void nextTick(() => closeButton.value?.focus());
});

onBeforeUnmount(() => {
	if (opener?.isConnected) {
		opener.focus();
	}
});

useEventListener(document, "keydown", (e: KeyboardEvent) => {
	if (e.key === "Escape") {
		e.preventDefault();
		emit("close");
	}
});

// Tabs: roving tabindex; arrows/Home/End select and focus the neighbouring tab.
const tabKeys = TABS.map(t => t.key);

function onTabKey(e: KeyboardEvent): void {
	const i = tabKeys.indexOf(tab.value);
	const last = tabKeys.length - 1;
	const targets: Record<string, number> = { ArrowLeft: i <= 0 ? last : i - 1, ArrowRight: i >= last ? 0 : i + 1, Home: 0, End: last };
	const next = targets[e.key];
	if (next === undefined) {
		return;
	}
	e.preventDefault();
	tab.value = tabKeys[next]!;
	(e.currentTarget as HTMLElement).querySelector<HTMLElement>(`#settings-tab-${tab.value}`)?.focus();
}

// Calendars / live engines load only once their tab is first opened.
let calendarsRequested = false;
let liveRequested = false;
watch(tab, (value) => {
	if (value === "calendar" && calendarEnabled && !calendarsRequested) {
		calendarsRequested = true;
		void loadCalendars();
	}
	if (value === "live" && !liveRequested) {
		liveRequested = true;
		void loadListenEngines();
		void checkDevice();
		void loadCorrectionEngines();
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
		<aside
			class="drawer__panel"
			role="dialog"
			aria-modal="true"
			aria-labelledby="settings-title"
		>
			<header class="drawer__head">
				<h2
					id="settings-title"
					class="drawer__title"
				>
					Einstellungen
				</h2>
				<button
					ref="closeButton"
					class="drawer__close"
					type="button"
					aria-label="Schließen"
					data-testid="settings-close"
					@click="emit('close')"
				>
					<svg
						width="20"
						height="20"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						stroke-linejoin="round"
						aria-hidden="true"
					>
						<line
							x1="18"
							y1="6"
							x2="6"
							y2="18"
						/>
						<line
							x1="6"
							y1="6"
							x2="18"
							y2="18"
						/>
					</svg>
				</button>
			</header>

			<div
				class="h-tabs drawer__tabs"
				role="tablist"
				aria-label="Einstellungen"
				@keydown="onTabKey"
			>
				<button
					v-for="t in TABS"
					:id="`settings-tab-${t.key}`"
					:key="t.key"
					class="h-tab"
					:class="{ 'h-tab--active': tab === t.key }"
					type="button"
					:tabindex="tab === t.key ? 0 : -1"
					role="tab"
					:aria-selected="tab === t.key"
					aria-controls="settings-panel"
					:data-testid="`settings-tab-${t.key}`"
					@click="tab = t.key"
				>
					{{ t.label }}
				</button>
			</div>
			<section
				v-if="tab === 'hub'"
				id="settings-panel"
				class="drawer__body"
				role="tabpanel"
				aria-labelledby="settings-tab-hub"
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
						@change="saveTokenToAccount"
					>
				</label>

				<div class="drawer__field">
					<span class="drawer__label">Hub-URL</span>
					<span
						class="drawer__readonly"
						data-testid="settings-hub-url"
					>{{ hubUrl }}</span>
				</div>

				<p
					v-if="tokenSync.enabled()"
					class="drawer__sub"
					data-testid="settings-hub-token-hint"
				>
					Der Token wird auf diesem Gerät und in deinem Konto gespeichert, damit er auf deinen anderen Geräten nicht erneut eingetragen werden muss.
				</p>
				<p
					v-else
					class="drawer__sub"
				>
					Der Token wird nur auf diesem Gerät gespeichert.
				</p>
				<p
					v-if="accountSync"
					class="drawer__sub"
					role="status"
					data-testid="settings-hub-token-sync"
				>
					{{ accountSync }}
				</p>

				<div class="drawer__field drawer__field--row">
					<span class="drawer__label">Expertenmodus</span>
					<HToggle
						v-model="hubSettings.expert"
						aria-label="Expertenmodus"
						data-testid="settings-expert"
					/>
				</div>
				<p class="drawer__sub">
					Zeigt bei Aufnahmen und Dokumenten, auf welchem Worker, mit welchem Modell und wie lange die Verarbeitung dauerte.
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
				id="settings-panel"
				class="drawer__body"
				role="tabpanel"
				aria-labelledby="settings-tab-calendar"
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

			<section
				v-else
				id="settings-panel"
				class="drawer__body"
				role="tabpanel"
				aria-labelledby="settings-tab-live"
			>
				<div
					class="drawer__field"
					role="radiogroup"
					aria-label="Live-Quelle"
				>
					<span class="drawer__label">Live-Quelle</span>
					<label class="drawer__radio">
						<input
							v-model="liveSettings.source"
							type="radio"
							name="settings-live-source"
							value="hub"
							data-testid="settings-live-source"
						>
						Hub-Stream
					</label>
					<label class="drawer__radio">
						<input
							v-model="liveSettings.source"
							type="radio"
							name="settings-live-source"
							value="device"
							data-testid="settings-live-source"
						>
						Auf dem Gerät (lokal)
					</label>
				</div>

				<label
					v-if="listenEngines.length"
					class="drawer__field"
				>
					<span class="drawer__label">Hub-Engine</span>
					<select
						v-model="liveSettings.hubEngine"
						class="drawer__input"
						data-testid="settings-live-engine"
					>
						<option value="">
							Hub-Standard ({{ listenDefault || "keiner" }})
						</option>
						<option
							v-for="e in listenEngines"
							:key="e.name"
							:value="e.name"
						>
							{{ e.name }}{{ e.private ? "" : " (Cloud)" }}
						</option>
					</select>
				</label>
				<p
					v-else-if="listenLoaded"
					class="drawer__notice"
					data-testid="settings-live-no-engines"
				>
					Dieser Hub bietet kein Live-Streaming an – in Einstellungen → Live die lokale Erkennung wählen.
					<template v-if="listenError">
						({{ listenError }})
					</template>
				</p>

				<div class="drawer__field">
					<span class="drawer__label">Lokale Spracherkennung</span>
					<span
						class="drawer__readonly"
						data-testid="settings-live-device"
					>{{ deviceLabel }}</span>
					<HButton
						v-if="deviceState === 'downloadable'"
						variant="secondary"
						:disabled="deviceInstalling"
						data-testid="settings-live-device-install"
						@click="installDevice"
					>
						Sprachpaket laden
					</HButton>
				</div>

				<p class="drawer__sub">
					Die Tastatur-Diktierfunktion des Telefons funktioniert unabhängig davon im Texteditor.
				</p>

				<div class="drawer__field drawer__field--row">
					<span class="drawer__label">Mit besserem Modell korrigieren</span>
					<HToggle
						v-model="liveSettings.correction"
						aria-label="Mit besserem Modell korrigieren"
						data-testid="settings-live-correction"
					/>
				</div>

				<template v-if="liveSettings.correction">
					<label class="drawer__field">
						<span class="drawer__label">Korrektur-Engine</span>
						<select
							v-model="liveSettings.correctionEngine"
							class="drawer__input"
							data-testid="settings-live-correction-engine"
						>
							<option value="">
								Hub-Standard
							</option>
							<option
								v-for="e in correctionEngines"
								:key="e.name"
								:value="e.name"
							>
								{{ e.name }}{{ e.private ? "" : " (Cloud)" }}
							</option>
						</select>
					</label>
					<label class="drawer__field">
						<span class="drawer__label">Abschnittslänge (Sekunden)</span>
						<input
							:value="liveSettings.chunkSeconds"
							class="drawer__input"
							type="number"
							:min="CHUNK_SECONDS_MIN"
							:max="CHUNK_SECONDS_MAX"
							inputmode="numeric"
							data-testid="settings-live-chunk"
							@change="setChunkSeconds(($event.target as HTMLInputElement).value)"
						>
					</label>
				</template>
			</section>

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
					@click="emit('close')"
				>
					Fertig
				</HButton>
			</footer>
		</aside>
	</div>
</template>

<style scoped>
/* Right-side panel at every width; 24 px edges, 20 px between groups. */
.drawer { position:fixed; inset:0; z-index:60; display:flex; justify-content:flex-end; }
.drawer__scrim { position:absolute; inset:0; background:rgba(35,25,17,0.72); animation:drawer-fade var(--motion-base) var(--motion-ease); }
.drawer__panel { position:relative; width:min(420px, 92vw); height:100%; background:var(--white); display:flex; flex-direction:column; box-shadow:var(--shadow-deep); animation:drawer-in var(--motion-base) var(--motion-ease); }
@keyframes drawer-fade { from { opacity:0; } }
@keyframes drawer-in { from { transform:translateX(24px); opacity:0; } }
@media (prefers-reduced-motion:reduce) { .drawer__scrim, .drawer__panel { animation:none; } }

.drawer__head { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:calc(16px + env(safe-area-inset-top)) 16px 4px 24px; }
.drawer__title { margin:0; font-family:var(--font-display); font-weight:600; font-size:var(--fs-lg); line-height:1.2; color:var(--fg1); }
.drawer__close { width:40px; height:40px; flex:0 0 auto; border:none; border-radius:var(--r-full); background:none; color:var(--fg2); display:inline-flex; align-items:center; justify-content:center; cursor:pointer; transition:background-color var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease); }
.drawer__close:hover { background:var(--cream-deep); color:var(--fg1); }
.drawer__close:active { background:var(--crust-soft); }
.drawer__tabs { padding:0 24px; }

.drawer__body { flex:1; overflow-y:auto; padding:24px; display:flex; flex-direction:column; gap:20px; }
.drawer__notice { margin:0; padding:12px 14px; border-radius:var(--r-md); background:var(--butter-soft); color:var(--butter-ink); font-size:var(--fs-sm); line-height:1.5; }
.drawer__connect { display:flex; flex-direction:column; gap:12px; align-items:flex-start; }
.drawer__hint { margin:0; font-size:var(--fs-sm); line-height:1.5; color:var(--fg1); }
.drawer__connect-btn { display:inline-flex; align-items:center; min-height:44px; padding:0 16px; border-radius:var(--r-sm); background:var(--accent); color:var(--fg-on-accent); font-weight:600; font-size:var(--fs-sm); text-decoration:none; transition:background-color var(--motion-fast) var(--motion-ease); }
.drawer__connect-btn:hover { background:var(--accent-hover); }

/* Fields keep their natural height; only side-by-side fields share a row. */
.drawer__field { display:flex; flex-direction:column; gap:8px; }
.drawer__row { display:flex; gap:12px; }
.drawer__row > .drawer__field { flex:1; min-width:0; }
.drawer__field--row { flex-direction:row; align-items:center; justify-content:space-between; gap:12px; }
.drawer__field + .drawer__sub { margin-top:-12px; }
.drawer__label { font-size:var(--fs-sm); font-weight:600; color:var(--fg2); }
.drawer__input { width:100%; min-height:44px; box-sizing:border-box; border:1.5px solid var(--border-strong); border-radius:var(--r-sm); padding:10px 12px; background:var(--white); font-family:var(--font-body); font-size:var(--fs-md); color:var(--fg1); transition:border-color var(--motion-fast) var(--motion-ease), box-shadow var(--motion-fast) var(--motion-ease); }
.drawer__input:hover { border-color:var(--crust); }
.drawer__input:focus-visible { outline:none; border-color:var(--accent); box-shadow:0 0 0 3px var(--focus-ring); }
.drawer__sub { margin:0; font-size:var(--fs-xs); line-height:1.5; color:var(--fg3); }
.drawer__readonly { font-size:var(--fs-sm); color:var(--fg2); word-break:break-all; }
.drawer__link { color:var(--accent-ink); font-weight:600; font-size:var(--fs-sm); text-decoration:none; border-radius:var(--r-xs); }
.drawer__link:hover { text-decoration:underline; text-underline-offset:3px; }
.drawer__radio { display:flex; align-items:center; gap:10px; min-height:32px; font-size:var(--fs-md); color:var(--fg1); cursor:pointer; }

.drawer__foot { padding:16px 24px calc(20px + env(safe-area-inset-bottom)); border-top:1px solid var(--border); display:flex; flex-direction:column; gap:12px; }
.drawer__foot > * { width:100%; }
.drawer__diag-link { text-align:center; font-size:var(--fs-xs); }
</style>
