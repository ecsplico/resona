<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { buildDebugPayload, describeError, runDiagnose } from "~/utils/debugProbe";
import { RECORDER_MIME_CANDIDATES } from "~/composables/useRecorder";
import { discardUnfinished } from "~/data/unfinishedRecordings";
import type { DebugReport, ProbeCheck, ProbeState } from "~/utils/debugProbe";

// Diagnostics screen. "Mobil-Diagnose" checks everything live dictation and
// the on-device title suggestion need and can send a redacted report to the
// server log (TEMPORARY, see server/api/debug-report.post.ts). The PTT
// sections exercise the *exact* same config/endpoint/fields the real PTT flow
// in recording/[id].vue uses, but — unlike PTT — never swallow the real error.

const cfg = useRuntimeConfig().public;
const hub = useHub();
const pttDiag = usePttDiagnostics();
const liveSettings = useLiveSettings();

const { back } = useBackNav();

function goBack(): void {
	void back("/");
}

// ── Config echo ─────────────────────────────────────────────────────────────
const isMac = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform);
const pttKeyLabel = isMac ? "ctrl (links)" : "Strg (links)";

const configRows = computed(() => [
	{ label: "Directus URL", value: cfg.directusUrl },
	{ label: "resona-api URL", value: cfg.resonaApiUrl },
	{ label: "Hub-Token gesetzt", value: hub.hasToken() ? "ja" : "nein" },
	{ label: "Standardsprache", value: cfg.defaultLanguage },
	{ label: "Standardprofil (nicht bei PTT gesendet)", value: cfg.defaultProfile },
	{ label: "PTT-Taste", value: `${pttKeyLabel} (code: ControlLeft)` },
	{ label: "Plattform", value: typeof navigator !== "undefined" ? navigator.platform : "unbekannt" },
]);

// ── Capability checks ───────────────────────────────────────────────────────
const secureContext = typeof window !== "undefined" ? window.isSecureContext : true;
const mediaDevicesSupported = typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia;
const mediaRecorderSupported = typeof MediaRecorder !== "undefined";
const audioContextSupported = typeof AudioContext !== "undefined" || typeof (globalThis as Record<string, unknown>).webkitAudioContext !== "undefined";

const mimeSupport = computed(() => RECORDER_MIME_CANDIDATES.map(type => ({
	type,
	supported: mediaRecorderSupported && typeof MediaRecorder.isTypeSupported === "function" && MediaRecorder.isTypeSupported(type),
})));

const micPermission = ref("wird geprüft…");

const deviceSpeech = useDeviceSpeech();
const deviceSpeechState = ref("wird geprüft…");

async function checkDeviceSpeech(): Promise<void> {
	try {
		deviceSpeechState.value = await deviceSpeech.availability("de");
	}
	catch (err) {
		deviceSpeechState.value = `Fehler (${describeError(err)})`;
	}
}

async function checkMicPermission(): Promise<void> {
	if (typeof navigator === "undefined" || !navigator.permissions?.query) {
		micPermission.value = "unbekannt (Permissions API nicht unterstützt)";
		return;
	}
	try {
		const status = await navigator.permissions.query({ name: "microphone" } as unknown as PermissionDescriptor);
		micPermission.value = status.state;
	}
	catch (err) {
		micPermission.value = `unbekannt (${describeError(err)})`;
	}
}

// ── Reachability checks ─────────────────────────────────────────────────────
interface CheckResult { state: "checking" | "ok" | "fail"; detail: string }

const directusCheck = ref<CheckResult>({ state: "checking", detail: "" });
const resonaHealthCheck = ref<CheckResult>({ state: "checking", detail: "" });
const pttEndpointCheck = ref<CheckResult>({ state: "checking", detail: "" });

async function pingWithStatus(url: string): Promise<CheckResult> {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), 5000);
	try {
		const resp = await fetch(url, { cache: "no-store", signal: controller.signal });
		return { state: resp.ok ? "ok" : "fail", detail: `HTTP ${resp.status} ${resp.statusText}` };
	}
	catch (err) {
		return { state: "fail", detail: describeError(err) };
	}
	finally {
		clearTimeout(timer);
	}
}

// A GET/health ping only proves the host answers. PTT posts multipart form
// data with an Authorization header straight from the browser to the hub —
// a cross-origin request that needs its own CORS allowance.
// This probes that exact endpoint so a CORS misconfiguration shows up here
// instead of as PTT's generic "Transkription fehlgeschlagen".
async function probePttEndpoint(): Promise<CheckResult> {
	try {
		const form = new FormData();
		form.append("file", new Blob([new Uint8Array(4)], { type: "audio/webm" }), "probe.webm");
		form.append("language", cfg.defaultLanguage);
		form.append("response_format", "json");
		const resp = await hub.probe("/v1/audio/transcriptions", { method: "POST", body: form });
		const bodyText = await resp.text().catch(() => "");
		// Any HTTP response — even a 4xx rejecting the fake 4-byte "audio" —
		// means the request reached the server and wasn't blocked by CORS.
		return { state: "ok", detail: `Server erreicht: HTTP ${resp.status}${bodyText ? ` — ${bodyText.slice(0, 200)}` : ""}` };
	}
	catch (err) {
		return { state: "fail", detail: `${describeError(err)} — meist CORS-Blockade oder falsche/nicht erreichbare URL` };
	}
}

async function runReachabilityChecks(): Promise<void> {
	directusCheck.value = { state: "checking", detail: "" };
	resonaHealthCheck.value = { state: "checking", detail: "" };
	pttEndpointCheck.value = { state: "checking", detail: "" };
	const [d, r, p] = await Promise.all([
		pingWithStatus(`${trimUrl(cfg.directusUrl)}/server/health`),
		pingWithStatus(`${trimUrl(cfg.resonaApiUrl)}/health`),
		probePttEndpoint(),
	]);
	directusCheck.value = d;
	resonaHealthCheck.value = r;
	pttEndpointCheck.value = p;
}

// ── Live test dictation (exercises the real PTT code path end-to-end) ──────
const testRecorder = useRecorder();
const testState = ref<"idle" | "recording" | "sending">("idle");
const testResult = ref<CheckResult | null>(null);
let testTimer: ReturnType<typeof setTimeout> | null = null;

async function runTestDictation(): Promise<void> {
	if (testState.value !== "idle") return;
	testResult.value = null;
	try {
		await testRecorder.start();
		testState.value = "recording";
		testTimer = setTimeout(() => void finishTestDictation(), 2000);
	}
	catch (err) {
		testResult.value = { state: "fail", detail: `Mikrofonzugriff fehlgeschlagen: ${describeError(err)}` };
	}
}

async function finishTestDictation(): Promise<void> {
	testTimer = null;
	testState.value = "sending";
	const blob = await testRecorder.stop();
	// A test dictation is never offered again: drop the native recorder's file.
	const session = testRecorder.sessionId.value;
	if (session) void discardUnfinished(session);
	if (!blob || blob.size === 0) {
		testResult.value = { state: "fail", detail: "Leere Aufnahme (0 Bytes) — MediaRecorder hat keine Daten geliefert." };
		testState.value = "idle";
		return;
	}
	try {
		const form = new FormData();
		form.append("file", blob, "test-dictation.webm");
		form.append("language", cfg.defaultLanguage);
		form.append("response_format", "json");
		const resp = await hub.probe("/v1/audio/transcriptions", { method: "POST", body: form });
		const bodyText = await resp.text();
		if (!resp.ok) {
			testResult.value = { state: "fail", detail: `HTTP ${resp.status} ${resp.statusText} — ${bodyText.slice(0, 300)}` };
		}
		else {
			let text = "";
			try {
				text = (JSON.parse(bodyText) as { text?: string }).text ?? "";
			}
			catch {
				// non-JSON body: report the raw text below instead
			}
			testResult.value = {
				state: "ok",
				detail: `HTTP ${resp.status}, ${blob.size} Bytes Audio → „${text || bodyText.slice(0, 200) || "(leerer Text)"}"`,
			};
		}
	}
	catch (err) {
		testResult.value = { state: "fail", detail: describeError(err) };
	}
	finally {
		testState.value = "idle";
	}
}

// ── Live key tester (confirms the browser actually delivers ControlLeft) ───
const lastKeyEvent = ref("noch keine Taste gedrückt");
const keyHeld = ref(false);

function onTestKeyDown(e: KeyboardEvent): void {
	keyHeld.value = e.code === "ControlLeft";
	lastKeyEvent.value = `keydown: code="${e.code}" key="${e.key}"`;
}

function onTestKeyUp(e: KeyboardEvent): void {
	if (e.code === "ControlLeft") keyHeld.value = false;
	lastKeyEvent.value = `keyup: code="${e.code}" key="${e.key}"`;
}

function formatTime(ts: number): string {
	return new Date(ts).toLocaleTimeString();
}

// ── Mobil-Diagnose (live dictation + on-device title) ───────────────────────
const diagRunning = ref(false);
const diagChecks = ref<ProbeCheck[]>([]);
const diagReport = ref<DebugReport | null>(null);
const diagError = ref<string | null>(null);
const sendState = ref<{ state: "idle" | "sending" | "ok" | "fail"; detail: string }>({ state: "idle", detail: "" });

const STATE_TONE: Record<ProbeState, "success" | "danger" | "warning" | "neutral"> = {
	ok: "success",
	fail: "danger",
	warn: "warning",
	info: "neutral",
};
const STATE_LABEL: Record<ProbeState, string> = { ok: "ok", fail: "Fehler", warn: "Warnung", info: "Info" };

// Not async on purpose: runDiagnose creates its comparison AudioContext
// synchronously inside this click (user gesture).
function startDiagnose(): void {
	if (diagRunning.value) return;
	diagRunning.value = true;
	diagChecks.value = [];
	diagReport.value = null;
	diagError.value = null;
	sendState.value = { state: "idle", detail: "" };
	runDiagnose({
		hubUrl: trimUrl(cfg.resonaApiUrl),
		language: cfg.defaultLanguage,
		token: hub.token(),
		hubFetch: hub.probe,
		liveSettings: {
			source: liveSettings.source,
			hubEngine: liveSettings.hubEngine,
			correction: liveSettings.correction,
			chunkSeconds: liveSettings.chunkSeconds,
		},
		context: {
			origin: window.location.origin,
			dataMode: cfg.dataMode,
			hasToken: hub.hasToken(),
		},
		pttLog: pttDiag.entries.value.map(e => ({ ...e })),
		onCheck: (check) => {
			diagChecks.value = [...diagChecks.value, check];
		},
	})
		.then((report) => {
			diagReport.value = report;
		})
		.catch((err: unknown) => {
			diagError.value = describeError(err);
		})
		.finally(() => {
			diagRunning.value = false;
		});
}

async function sendReport(): Promise<void> {
	if (!diagReport.value || sendState.value.state === "sending") return;
	sendState.value = { state: "sending", detail: "" };
	try {
		// Plain fetch, not $fetch: the route is typed into Nitro's route union
		// from its file either way, but a plain fetch adds no call that TS has
		// to resolve against that union (see the Nitro route note in CLAUDE.md).
		const resp = await fetch("/api/debug-report", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: buildDebugPayload(diagReport.value, [hub.token()]),
		});
		if (!resp.ok) {
			sendState.value = { state: "fail", detail: `HTTP ${resp.status} ${resp.statusText}` };
			return;
		}
		const { id } = await resp.json() as { id?: string };
		sendState.value = { state: "ok", detail: `Gesendet. Bericht-ID ${id ?? "?"} (Server-Log: [resona-debug])` };
	}
	catch (err) {
		sendState.value = { state: "fail", detail: describeError(err) };
	}
}

onMounted(() => {
	void checkMicPermission();
	void checkDeviceSpeech();
	void runReachabilityChecks();
	window.addEventListener("keydown", onTestKeyDown);
	window.addEventListener("keyup", onTestKeyUp);
});

onUnmounted(() => {
	window.removeEventListener("keydown", onTestKeyDown);
	window.removeEventListener("keyup", onTestKeyUp);
	if (testTimer) clearTimeout(testTimer);
});
</script>

<template>
	<div class="debug">
		<HAppBar
			tone="light"
			title="Diagnose"
		>
			<template #leading>
				<HIconButton
					tone="cream"
					aria-label="Zurück"
					data-testid="debug-back"
					@click="goBack"
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
						<path d="M15 18l-6-6 6-6" />
					</svg>
				</HIconButton>
			</template>
		</HAppBar>

		<div class="debug__body">
			<!-- Mobil-Diagnose -->
			<HCard>
				<div class="debug__head-row">
					<h2 class="debug__title">
						Mobil-Diagnose: Live-Diktat &amp; Titelvorschlag
					</h2>
				</div>
				<p class="debug__hint">
					Prüft Mikrofon, Audioverarbeitung, Live-Verbindung zum Hub, lokale Spracherkennung und das
					Sprachmodell des Browsers. Dauert etwa 15 Sekunden; bitte währenddessen kurz sprechen.
					„An Server senden“ schickt das Ergebnis ohne Token und ohne Transkripttext ins Server-Log.
				</p>
				<div class="debug__actions">
					<HButton
						variant="primary"
						:disabled="diagRunning"
						data-testid="debug-diagnose-run"
						@click="startDiagnose"
					>
						{{ diagRunning ? "Prüft…" : "Diagnose starten" }}
					</HButton>
					<HButton
						variant="secondary"
						:disabled="!diagReport || sendState.state === 'sending'"
						data-testid="debug-diagnose-send"
						@click="sendReport"
					>
						{{ sendState.state === "sending" ? "Sendet…" : "An Server senden" }}
					</HButton>
				</div>
				<p
					v-if="sendState.state === 'ok' || sendState.state === 'fail'"
					class="debug__result"
					data-testid="debug-diagnose-send-result"
				>
					<HChip :tone="sendState.state === 'ok' ? 'success' : 'danger'">
						{{ sendState.state === "ok" ? "Gesendet" : "Fehler" }}
					</HChip>
					{{ sendState.detail }}
				</p>
				<p
					v-if="diagError"
					class="debug__result"
				>
					<HChip tone="danger">
						Abbruch
					</HChip>
					{{ diagError }}
				</p>
				<ul
					v-if="diagChecks.length"
					class="debug__diag"
					data-testid="debug-diagnose-checks"
				>
					<li
						v-for="check in diagChecks"
						:key="check.id"
						class="debug__diag-row"
						:data-check="check.id"
					>
						<HChip :tone="STATE_TONE[check.state]">
							{{ STATE_LABEL[check.state] }}
						</HChip>
						<div class="debug__diag-text">
							<span class="debug__diag-label">{{ check.group }} · {{ check.label }}</span>
							<span class="debug__diag-detail">{{ check.detail }}</span>
						</div>
					</li>
				</ul>
			</HCard>

			<!-- Konfiguration -->
			<HCard>
				<h2 class="debug__title">
					Konfiguration
				</h2>
				<dl class="debug__rows">
					<template
						v-for="row in configRows"
						:key="row.label"
					>
						<dt>{{ row.label }}</dt>
						<dd data-testid="debug-config-value">
							{{ row.value }}
						</dd>
					</template>
				</dl>
			</HCard>

			<!-- Umgebung & Berechtigungen -->
			<HCard>
				<h2 class="debug__title">
					Umgebung &amp; Berechtigungen
				</h2>
				<dl class="debug__rows">
					<dt>Sicherer Kontext (HTTPS/localhost)</dt>
					<dd>
						<HChip :tone="secureContext ? 'success' : 'danger'">
							{{ secureContext ? "ja" : "nein — getUserMedia wird blockiert" }}
						</HChip>
					</dd>
					<dt>Mikrofonzugriff (getUserMedia)</dt>
					<dd>
						<HChip :tone="mediaDevicesSupported ? 'success' : 'danger'">
							{{ mediaDevicesSupported ? "unterstützt" : "nicht unterstützt" }}
						</HChip>
					</dd>
					<dt>Mikrofonberechtigung</dt>
					<dd data-testid="debug-mic-permission">
						{{ micPermission }}
					</dd>
					<dt>MediaRecorder</dt>
					<dd>
						<HChip :tone="mediaRecorderSupported ? 'success' : 'danger'">
							{{ mediaRecorderSupported ? "unterstützt" : "nicht unterstützt" }}
						</HChip>
					</dd>
					<dt>Unterstützte Audioformate</dt>
					<dd>
						<span
							v-for="m in mimeSupport"
							:key="m.type"
							class="debug__mime"
						>
							<HChip :tone="m.supported ? 'success' : 'neutral'">
								{{ m.type }}
							</HChip>
						</span>
					</dd>
					<dt>AudioContext (Pegelanzeige, unkritisch)</dt>
					<dd>
						<HChip :tone="audioContextSupported ? 'success' : 'warning'">
							{{ audioContextSupported ? "unterstützt" : "nicht unterstützt (Pegelanzeige fällt aus)" }}
						</HChip>
					</dd>
					<dt>Lokale Spracherkennung (de)</dt>
					<dd data-testid="debug-device-speech">
						{{ deviceSpeechState }}
					</dd>
				</dl>
			</HCard>

			<!-- Erreichbarkeit -->
			<HCard>
				<div class="debug__head-row">
					<h2 class="debug__title">
						Erreichbarkeit
					</h2>
					<HButton
						variant="secondary"
						data-testid="debug-recheck"
						@click="runReachabilityChecks"
					>
						Neu prüfen
					</HButton>
				</div>
				<dl class="debug__rows">
					<dt>Directus (/server/health)</dt>
					<dd>
						<HChip :tone="directusCheck.state === 'ok' ? 'success' : directusCheck.state === 'fail' ? 'danger' : 'neutral'">
							{{ directusCheck.state === "checking" ? "prüft…" : directusCheck.detail }}
						</HChip>
					</dd>
					<dt>resona-api (/health)</dt>
					<dd>
						<HChip :tone="resonaHealthCheck.state === 'ok' ? 'success' : resonaHealthCheck.state === 'fail' ? 'danger' : 'neutral'">
							{{ resonaHealthCheck.state === "checking" ? "prüft…" : resonaHealthCheck.detail }}
						</HChip>
					</dd>
					<dt>PTT-Endpunkt (/v1/audio/transcriptions, CORS-Test)</dt>
					<dd data-testid="debug-ptt-endpoint-check">
						<HChip :tone="pttEndpointCheck.state === 'ok' ? 'success' : pttEndpointCheck.state === 'fail' ? 'danger' : 'neutral'">
							{{ pttEndpointCheck.state === "checking" ? "prüft…" : pttEndpointCheck.detail }}
						</HChip>
					</dd>
				</dl>
			</HCard>

			<!-- Tasten-Test -->
			<HCard>
				<h2 class="debug__title">
					Tasten-Test
				</h2>
				<p class="debug__hint">
					Halte die PTT-Taste ({{ pttKeyLabel }}) — hier sollte sofort "keydown" mit code="ControlLeft" erscheinen.
					Kommt nichts an, fängt Betriebssystem/Browser die Taste vorher ab.
				</p>
				<p
					class="debug__key-state"
					data-testid="debug-key-state"
				>
					<HChip :tone="keyHeld ? 'success' : 'neutral'">
						{{ keyHeld ? "gedrückt" : "losgelassen" }}
					</HChip>
					{{ lastKeyEvent }}
				</p>
			</HCard>

			<!-- Testdiktat -->
			<HCard>
				<h2 class="debug__title">
					Testdiktat (2 Sekunden, identischer Ablauf wie PTT)
				</h2>
				<p class="debug__hint">
					Nimmt 2 Sekunden Audio auf und sendet sie genau wie die echte Diktierfunktion an resona-api —
					zeigt aber die vollständige Antwort/den vollständigen Fehler statt der generischen Meldung.
				</p>
				<HButton
					variant="primary"
					:disabled="testState !== 'idle'"
					data-testid="debug-run-test"
					@click="runTestDictation"
				>
					{{ testState === "recording" ? "Nimmt auf…" : testState === "sending" ? "Sendet…" : "Testaufnahme starten" }}
				</HButton>
				<p
					v-if="testResult"
					class="debug__result"
					data-testid="debug-test-result"
				>
					<HChip :tone="testResult.state === 'ok' ? 'success' : 'danger'">
						{{ testResult.state === "ok" ? "Erfolg" : "Fehler" }}
					</HChip>
					{{ testResult.detail }}
				</p>
			</HCard>

			<!-- Letzte PTT-Versuche -->
			<HCard>
				<div class="debug__head-row">
					<h2 class="debug__title">
						Letzte PTT-Versuche (diese Sitzung)
					</h2>
					<HButton
						variant="secondary"
						data-testid="debug-clear-log"
						@click="pttDiag.clear()"
					>
						Leeren
					</HButton>
				</div>
				<p
					v-if="pttDiag.entries.value.length === 0"
					class="debug__hint"
				>
					Noch keine Diktierversuche auf der Aufnahme-Seite in dieser Sitzung.
				</p>
				<ul
					v-else
					class="debug__log"
					data-testid="debug-ptt-log"
				>
					<li
						v-for="(entry, i) in pttDiag.entries.value"
						:key="i"
						class="debug__log-row"
					>
						<span class="debug__log-time">{{ formatTime(entry.ts) }}</span>
						<HChip :tone="entry.ok ? 'success' : 'danger'">
							{{ entry.stage }}
						</HChip>
						<span class="debug__log-detail">{{ entry.detail }}</span>
					</li>
				</ul>
			</HCard>
		</div>
	</div>
</template>

<style scoped>
.debug { flex:1; display:flex; flex-direction:column; background:var(--bg); }
/* Diagnostics read like text: a centered ~72ch column under the full-width bar. */
.debug__body { flex:1; display:flex; flex-direction:column; gap:14px; width:100%; max-width:calc(72ch + 40px); margin:0 auto; padding:20px; }
.debug__title { margin:0 0 10px; font-family:var(--font-display); font-weight:600; font-size:16px; color:var(--fg1); }
.debug__head-row { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:2px; }
.debug__head-row .debug__title { margin-bottom:0; }
.debug__rows { display:grid; grid-template-columns:minmax(160px, 1fr) auto; gap:8px 14px; align-items:center; margin:0; }
.debug__rows dt { font-size:13px; color:var(--fg3); }
.debug__rows dd { margin:0; font-size:14px; color:var(--fg1); text-align:right; word-break:break-word; }
.debug__mime { margin-left:6px; display:inline-block; }
.debug__mime:first-child { margin-left:0; }
.debug__hint { margin:0 0 10px; font-size:13px; color:var(--fg3); line-height:1.5; }
.debug__key-state { display:flex; align-items:center; gap:10px; font-size:13px; color:var(--fg2); margin:0; }
.debug__result { display:flex; align-items:flex-start; gap:10px; font-size:13px; color:var(--fg2); margin:10px 0 0; }
.debug__log { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:8px; }
.debug__log-row { display:flex; align-items:flex-start; gap:10px; font-size:13px; }
.debug__log-time { color:var(--fg3); flex-shrink:0; }
.debug__log-detail { color:var(--fg1); word-break:break-word; }
.debug__actions { display:flex; flex-wrap:wrap; gap:10px; }
.debug__diag { list-style:none; margin:14px 0 0; padding:0; display:flex; flex-direction:column; gap:10px; }
.debug__diag-row { display:flex; align-items:flex-start; gap:10px; font-size:13px; }
.debug__diag-text { display:flex; flex-direction:column; gap:2px; min-width:0; }
.debug__diag-label { color:var(--fg3); }
.debug__diag-detail { color:var(--fg1); word-break:break-word; }
</style>
