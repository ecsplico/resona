/**
 * TEMPORARY mobile diagnostics for `/debug` → "Diagnose": checks everything
 * live dictation (`live.vue`: getUserMedia → `usePcmCapture` → hub `/v1/listen`,
 * or on-device Web Speech) and the planned on-device title suggestion
 * (Prompt API, issues #3–#6) need, in the same order and with the same
 * parameters the real code uses. Each check records ok/fail/warn/info plus a
 * German detail; `buildDebugPayload` redacts the report (no token, no
 * transcript text) for `POST /api/debug-report`.
 *
 * Every browser API is feature-detected: the module must not throw under
 * happy-dom or on browsers missing an API (that absence *is* the finding).
 */
import { DEBUG_REPORT_MAX_BYTES, byteLength, redactDebugReport } from "#shared/debugReport";
import { floatTo16kPCM } from "~/utils/pcm";
import { RECORDER_MIME_CANDIDATES } from "~/composables/useRecorder";

export type ProbeState = "ok" | "fail" | "warn" | "info";

export interface ProbeCheck {
	id: string;
	group: string;
	label: string;
	state: ProbeState;
	detail: string;
	data?: Record<string, unknown>;
}

export interface DebugReport {
	version: 1;
	createdAt: string;
	durationMs: number;
	context: Record<string, unknown>;
	checks: ProbeCheck[];
	pttLog: { ts: number; stage: string; ok: boolean; detail: string }[];
}

export interface DiagnoseDeps {
	/** Hub base URL (`runtimeConfig.public.resonaApiUrl`). */
	hubUrl: string;
	language: string;
	/** Device hub token; only used for the requests, never stored in the report. */
	token: string;
	/** Raw authenticated hub fetch (`useHub().probe`). */
	hubFetch: (path: string, init?: RequestInit) => Promise<Response>;
	liveSettings: { source: string; hubEngine: string; correction: boolean; chunkSeconds: number };
	context?: Record<string, unknown>;
	pttLog?: DebugReport["pttLog"];
	/** Called after each check so the page can render progress. */
	onCheck?: (check: ProbeCheck) => void;
}

const PCM_WINDOW_MS = 2000;
const SOCKET_TIMEOUT_MS = 10000;

type AnyRecord = Record<string, unknown>;

function g(): AnyRecord {
	return globalThis as unknown as AnyRecord;
}

function nav(): (Navigator & AnyRecord) | undefined {
	return (globalThis as { navigator?: Navigator }).navigator as (Navigator & AnyRecord) | undefined;
}

/** `Name: message` of an Error, else its string form; shown in checks and on `/debug`. */
export function describeError(err: unknown): string {
	return err instanceof Error ? `${err.name}: ${err.message}` : String(err);
}

/** Resolves to `{ timedOut: true }` instead of hanging (iOS `resume()` can). */
async function settle<T>(promise: Promise<T>, ms: number): Promise<{ value?: T; error?: unknown; timedOut: boolean }> {
	let timer: ReturnType<typeof setTimeout> | undefined;
	const timeout = new Promise<{ timedOut: true }>((resolve) => {
		timer = setTimeout(() => resolve({ timedOut: true }), ms);
	});
	try {
		return await Promise.race([
			promise.then(value => ({ value, timedOut: false }), error => ({ error, timedOut: false })),
			timeout,
		]);
	}
	finally {
		clearTimeout(timer);
	}
}

// ── Device ────────────────────────────────────────────────────────────────

export interface OsGuess { os: string; version: string | null; mobile: boolean }

/** Best-effort OS + version from the UA (iPadOS reports itself as macOS). */
export function guessDeviceOs(ua: string, maxTouchPoints = 0): OsGuess {
	const ios = /(?:iPhone|iPad|iPod).*? OS (\d+)[_.](\d+)(?:[_.](\d+))?/.exec(ua);
	if (ios) {
		return { os: /iPad/.test(ua) ? "iPadOS" : "iOS", version: [ios[1], ios[2], ios[3]].filter(Boolean).join("."), mobile: true };
	}
	const android = /Android (\d+(?:\.\d+)*)/.exec(ua);
	if (android) {
		return { os: "Android", version: android[1] ?? null, mobile: true };
	}
	const mac = /Mac OS X (\d+)[_.](\d+)/.exec(ua);
	if (mac) {
		// iPadOS 13+ sends a desktop Safari UA; touch points give it away. Its
		// "Version/x" is the Safari = iPadOS major.
		if (maxTouchPoints > 1) {
			return { os: "iPadOS (Desktop-UA)", version: /Version\/(\d+(?:\.\d+)*)/.exec(ua)?.[1] ?? null, mobile: true };
		}
		return { os: "macOS", version: `${mac[1]}.${mac[2]}`, mobile: false };
	}
	if (/Windows/.test(ua)) return { os: "Windows", version: null, mobile: false };
	if (/Linux/.test(ua)) return { os: "Linux", version: null, mobile: false };
	return { os: "unbekannt", version: null, mobile: /Mobi/.test(ua) };
}

function deviceChecks(): ProbeCheck[] {
	const n = nav();
	const ua = n?.userAgent ?? "";
	const os = guessDeviceOs(ua, n?.maxTouchPoints ?? 0);
	const uaData = n?.userAgentData as { brands?: { brand: string; version: string }[]; mobile?: boolean; platform?: string } | undefined;
	const matchMedia = (globalThis as { matchMedia?: (q: string) => MediaQueryList }).matchMedia;
	const standalone = !!matchMedia?.("(display-mode: standalone)").matches || n?.standalone === true;
	const secure = (globalThis as { isSecureContext?: boolean }).isSecureContext === true;
	const doc = (globalThis as { document?: Document }).document;
	const sw = n?.serviceWorker as ServiceWorkerContainer | undefined;
	const controller = sw?.controller ?? null;
	return [
		{
			id: "user-agent",
			group: "Gerät",
			label: "User-Agent",
			state: "info",
			detail: ua || "(leer)",
			data: { platform: n?.platform ?? null, brands: uaData?.brands ?? null, uaMobile: uaData?.mobile ?? null, uaPlatform: uaData?.platform ?? null },
		},
		{
			id: "os",
			group: "Gerät",
			label: "Betriebssystem (geschätzt)",
			state: "info",
			detail: `${os.os}${os.version ? ` ${os.version}` : ""}${os.mobile ? " · mobil" : ""}`,
			data: { ...os, maxTouchPoints: n?.maxTouchPoints ?? null, language: n?.language ?? null },
		},
		{
			id: "standalone",
			group: "Gerät",
			label: "Als App installiert (display-mode standalone)",
			state: "info",
			detail: standalone ? "ja" : "nein (Browser-Tab)",
			data: { standalone },
		},
		{
			id: "secure-context",
			group: "Gerät",
			label: "Sicherer Kontext (HTTPS)",
			state: secure ? "ok" : "fail",
			detail: secure ? "ja" : "nein: Mikrofon und Service Worker sind blockiert",
		},
		{
			id: "visibility",
			group: "Gerät",
			label: "Sichtbarkeit",
			state: "info",
			detail: doc?.visibilityState ?? "unbekannt",
		},
		{
			id: "service-worker",
			group: "Gerät",
			label: "Service Worker",
			state: !sw ? "warn" : controller ? "ok" : "warn",
			detail: !sw
				? "nicht unterstützt"
				: controller
					? `steuert die Seite (${controller.state})`
					: "unterstützt, steuert die Seite aber nicht (erster Aufruf oder harte Neuladung)",
			data: { supported: !!sw, controlled: !!controller, scriptURL: controller?.scriptURL ?? null },
		},
	];
}

async function wakeLockCheck(): Promise<ProbeCheck> {
	// Typed as always present, but absent on older Safari/Firefox.
	const wl: WakeLock | undefined = nav()?.wakeLock;
	const base = { id: "wake-lock", group: "Gerät", label: "Wake Lock (Bildschirm bleibt an)" };
	if (!wl?.request) {
		return { ...base, state: "warn", detail: "nicht unterstützt: Bildschirm kann während des Diktats ausgehen" };
	}
	const res = await settle(wl.request("screen"), 3000);
	if (res.value) {
		await res.value.release().catch(() => {});
		return { ...base, state: "ok", detail: "Anfrage erfolgreich (wieder freigegeben)" };
	}
	return { ...base, state: "warn", detail: res.timedOut ? "Anfrage hängt (>3 s)" : `Anfrage abgelehnt: ${describeError(res.error)}` };
}

// ── Microphone + audio pipeline ───────────────────────────────────────────

async function micPermissionCheck(): Promise<ProbeCheck> {
	const base = { id: "mic-permission", group: "Mikrofon", label: "Berechtigung (Permissions API)" };
	const perms = nav()?.permissions;
	if (!perms?.query) {
		return { ...base, state: "info", detail: "Permissions API nicht vorhanden (Safari < 16 u. a.)" };
	}
	const res = await settle(perms.query({ name: "microphone" } as unknown as PermissionDescriptor), 3000);
	if (res.value) {
		const state = res.value.state;
		return { ...base, state: state === "denied" ? "fail" : state === "granted" ? "ok" : "info", detail: state };
	}
	return { ...base, state: "info", detail: res.timedOut ? "Abfrage hängt" : `nicht abfragbar: ${describeError(res.error)}` };
}

function audioContextCtor(): typeof AudioContext | undefined {
	return (g().AudioContext ?? g().webkitAudioContext) as typeof AudioContext | undefined;
}

// The recorder's own candidates first, then formats other browsers may offer instead.
const MIME_CANDIDATES: readonly string[] = [...RECORDER_MIME_CANDIDATES, "audio/mp4;codecs=mp4a.40.2", "audio/aac", "audio/wav"];

function mimeCheck(): ProbeCheck {
	const MR = g().MediaRecorder as typeof MediaRecorder | undefined;
	const base = { id: "media-recorder-mime", group: "Mikrofon", label: "MediaRecorder-Formate" };
	if (!MR) {
		return { ...base, state: "fail", detail: "MediaRecorder fehlt: Live-Audio wird nicht gespeichert" };
	}
	const supported = typeof MR.isTypeSupported === "function" ? MIME_CANDIDATES.filter(t => MR.isTypeSupported(t)) : [];
	// useRecorder takes the first of its candidates that is supported.
	const chosen = RECORDER_MIME_CANDIDATES.find(t => supported.includes(t)) ?? "";
	return {
		...base,
		state: supported.length ? "ok" : "warn",
		detail: supported.length ? `${supported.join(", ")} · App nimmt ${chosen || "Browser-Standard"}` : "kein Kandidat unterstützt (Browser-Standard)",
		data: { supported, chosen },
	};
}

interface PcmRun { frames: number; samples: number; peak: number; error: string | null }

/** usePcmCapture's graph on `ctx`: source → ScriptProcessor(4096) → destination. */
async function runPcmWindow(ctx: AudioContext, stream: MediaStream, keep: Int16Array[] | null): Promise<PcmRun> {
	const run: PcmRun = { frames: 0, samples: 0, peak: 0, error: null };
	let nodes: { source: MediaStreamAudioSourceNode; processor: ScriptProcessorNode };
	try {
		const source = ctx.createMediaStreamSource(stream);
		const processor = ctx.createScriptProcessor(4096, 1, 1);
		processor.onaudioprocess = (e: AudioProcessingEvent) => {
			const input = e.inputBuffer.getChannelData(0);
			for (let i = 0; i < input.length; i += 1) {
				const v = Math.abs(input[i] ?? 0);
				if (v > run.peak) run.peak = v;
			}
			const pcm = floatTo16kPCM(input, ctx.sampleRate, 16000);
			run.frames += 1;
			run.samples += pcm.length;
			keep?.push(pcm);
		};
		source.connect(processor);
		processor.connect(ctx.destination);
		nodes = { source, processor };
	}
	catch (err) {
		run.error = describeError(err);
		return run;
	}
	await new Promise(resolve => setTimeout(resolve, PCM_WINDOW_MS));
	try {
		nodes.processor.onaudioprocess = null;
		nodes.processor.disconnect();
		nodes.source.disconnect();
	}
	catch {
		// already detached
	}
	run.peak = Math.round(run.peak * 1000) / 1000;
	return run;
}

function pcmCheck(id: string, label: string, ctx: AudioContext, run: PcmRun): ProbeCheck {
	const perSecond = Math.round(run.samples / (PCM_WINDOW_MS / 1000));
	let state: ProbeState = "ok";
	let detail = `${run.frames} Frames in ${PCM_WINDOW_MS / 1000} s, ≈ ${perSecond} Samples/s (Soll 16000), Pegel max ${run.peak}`;
	if (run.error) {
		state = "fail";
		detail = `Audiograph nicht aufbaubar: ${run.error}`;
	}
	else if (run.frames === 0) {
		state = "fail";
		detail = `0 Frames in ${PCM_WINDOW_MS / 1000} s (AudioContext ${ctx.state}): an den Hub ginge kein Audio`;
	}
	else if (run.peak === 0) {
		state = "warn";
		detail += " · nur Stille (Mikrofon stumm/belegt?)";
	}
	else if (perSecond < 12000) {
		state = "warn";
		detail += " · zu wenige Samples, Audio stockt";
	}
	return { id, group: "Audio", label, state, detail, data: { ...run, perSecond, contextState: ctx.state, contextRate: ctx.sampleRate } };
}

async function closeContext(ctx: AudioContext | null): Promise<void> {
	if (ctx && ctx.state !== "closed") {
		await settle(ctx.close(), 1000);
	}
}

async function mediaRecorderRun(stream: MediaStream): Promise<{ check: ProbeCheck; stop: () => Promise<void> }> {
	const MR = g().MediaRecorder as typeof MediaRecorder | undefined;
	const base = { id: "media-recorder-run", group: "Audio", label: "MediaRecorder parallel (wie Live)" };
	const noop = { stop: async () => {} };
	if (!MR) {
		return { ...noop, check: { ...base, state: "fail", detail: "MediaRecorder fehlt" } };
	}
	const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"].find(t => typeof MR.isTypeSupported === "function" && MR.isTypeSupported(t)) ?? "";
	let rec: MediaRecorder;
	try {
		rec = new MR(stream, mime ? { mimeType: mime } : undefined);
	}
	catch (err) {
		return { ...noop, check: { ...base, state: "fail", detail: `Konstruktor: ${describeError(err)}` } };
	}
	let bytes = 0;
	let chunks = 0;
	let recError: string | null = null;
	rec.ondataavailable = (e: BlobEvent) => {
		chunks += 1;
		bytes += e.data?.size ?? 0;
	};
	rec.onerror = (e: Event) => {
		recError = describeError((e as unknown as { error?: unknown }).error ?? e.type);
	};
	try {
		rec.start(250);
	}
	catch (err) {
		return { ...noop, check: { ...base, state: "fail", detail: `start(): ${describeError(err)}` } };
	}
	const check: ProbeCheck = { ...base, state: "info", detail: "läuft…" };
	async function stop(): Promise<void> {
		const stopped = new Promise<void>((resolve) => {
			rec.onstop = () => resolve();
		});
		try {
			if (rec.state !== "inactive") rec.stop();
		}
		catch (err) {
			recError = describeError(err);
		}
		await settle(stopped, 2000);
		check.state = recError ? "fail" : bytes > 0 ? "ok" : "warn";
		check.detail = recError
			? `Fehler: ${recError}`
			: `${bytes} Bytes in ${chunks} Teilen (${rec.mimeType || mime || "Standard"})${bytes > 0 ? "" : ": keine Daten, Audio würde nicht gespeichert"}`;
		check.data = { mime: rec.mimeType || mime, bytes, chunks };
	}
	return { check, stop };
}

/**
 * Mirrors live.vue's start: getUserMedia once, then usePcmCapture creates its
 * AudioContext *after* that await (outside the user gesture) and resumes it,
 * while useRecorder records the same stream. `gestureCtx` was created
 * synchronously in the click handler, for comparison when the live-like
 * context stays silent (iOS autoplay policy). Pushes its checks; returns the
 * captured 16 kHz PCM, which the socket check streams to the hub.
 */
async function audioChecks(gestureCtx: AudioContext | null, push: (c: ProbeCheck) => void): Promise<Int16Array[]> {
	const pcm: Int16Array[] = [];
	const media = nav()?.mediaDevices;
	if (!media?.getUserMedia) {
		push({ id: "get-user-media", group: "Mikrofon", label: "getUserMedia", state: "fail", detail: "nicht vorhanden (unsicherer Kontext oder alter Browser)" });
		await closeContext(gestureCtx);
		return pcm;
	}
	const t0 = performance.now();
	const gum = await settle(media.getUserMedia({ audio: true }), 15000);
	const stream = gum.value;
	if (!stream) {
		push({
			id: "get-user-media",
			group: "Mikrofon",
			label: "getUserMedia",
			state: "fail",
			detail: gum.timedOut ? "keine Antwort in 15 s (Dialog nicht bestätigt?)" : describeError(gum.error),
		});
		await closeContext(gestureCtx);
		return pcm;
	}
	const track = stream.getAudioTracks()[0];
	const settings = (track?.getSettings?.() ?? {}) as MediaTrackSettings & AnyRecord;
	push({
		id: "get-user-media",
		group: "Mikrofon",
		label: "getUserMedia",
		state: track ? "ok" : "fail",
		detail: track
			? `Spur „${track.readyState}“, ${settings.sampleRate ?? "?"} Hz, ${settings.channelCount ?? "?"} Kanal/Kanäle (${Math.round(performance.now() - t0)} ms)`
			: "Stream ohne Audiospur",
		data: {
			ms: Math.round(performance.now() - t0),
			readyState: track?.readyState ?? null,
			muted: track?.muted ?? null,
			sampleRate: settings.sampleRate ?? null,
			channelCount: settings.channelCount ?? null,
			echoCancellation: settings.echoCancellation ?? null,
			noiseSuppression: settings.noiseSuppression ?? null,
			autoGainControl: settings.autoGainControl ?? null,
			sampleSize: settings.sampleSize ?? null,
			latency: settings.latency ?? null,
		},
	});

	const recorder = await mediaRecorderRun(stream);
	const Ctor = audioContextCtor();
	let liveCtx: AudioContext | null = null;
	try {
		if (!Ctor) {
			push({ id: "audio-context", group: "Audio", label: "AudioContext", state: "fail", detail: "AudioContext fehlt: kein PCM für den Hub" });
		}
		else {
			liveCtx = new Ctor();
			const before = liveCtx.state;
			let resumeNote = "";
			if (liveCtx.state === "suspended") {
				const res = await settle(liveCtx.resume(), 2000);
				resumeNote = res.timedOut ? " · resume() hängt >2 s" : res.error ? ` · resume() abgelehnt: ${describeError(res.error)}` : " · resume() ok";
			}
			push({
				id: "audio-context",
				group: "Audio",
				label: "AudioContext nach getUserMedia (wie Live)",
				state: liveCtx.state === "running" ? "ok" : "fail",
				detail: `${before} → ${liveCtx.state}, ${liveCtx.sampleRate} Hz${resumeNote}`,
				data: { before, after: liveCtx.state, sampleRate: liveCtx.sampleRate, baseLatency: liveCtx.baseLatency ?? null },
			});
			const hasScriptProcessor = typeof liveCtx.createScriptProcessor === "function";
			const hasWorklet = typeof g().AudioWorkletNode !== "undefined" && !!liveCtx.audioWorklet;
			push({
				id: "audio-nodes",
				group: "Audio",
				label: "ScriptProcessorNode / AudioWorklet",
				state: hasScriptProcessor ? "ok" : "fail",
				detail: `ScriptProcessor ${hasScriptProcessor ? "ja" : "NEIN (Live nutzt ihn)"} · AudioWorklet ${hasWorklet ? "ja" : "nein"}`,
				data: { hasScriptProcessor, hasWorklet },
			});
			if (hasScriptProcessor) {
				const run = await runPcmWindow(liveCtx, stream, pcm);
				push(pcmCheck("pcm-frames", "PCM-Frames in 2 s (wie Live)", liveCtx, run));
				if (run.frames === 0 && gestureCtx) {
					if (gestureCtx.state === "suspended") {
						await settle(gestureCtx.resume(), 2000);
					}
					const alt = await runPcmWindow(gestureCtx, stream, null);
					push(pcmCheck("pcm-frames-gesture", "PCM-Frames mit AudioContext aus der Nutzergeste", gestureCtx, alt));
				}
			}
		}
	}
	finally {
		await recorder.stop();
		push(recorder.check);
		await closeContext(liveCtx);
		await closeContext(gestureCtx);
		for (const t of stream.getTracks()) t.stop();
	}
	return pcm;
}

// ── Hub ───────────────────────────────────────────────────────────────────

function toWsUrl(httpUrl: string): string {
	if (httpUrl.startsWith("https:")) return `wss:${httpUrl.slice("https:".length)}`;
	if (httpUrl.startsWith("http:")) return `ws:${httpUrl.slice("http:".length)}`;
	return httpUrl;
}

interface ListenEngineLite { name: string; kind: string; private: boolean }

async function hubChecks(deps: DiagnoseDeps): Promise<{ checks: ProbeCheck[]; engines: ListenEngineLite[] }> {
	const checks: ProbeCheck[] = [];
	const health = await settle(fetch(`${deps.hubUrl}/health`, { cache: "no-store" }), 8000);
	checks.push({
		id: "hub-health",
		group: "Hub",
		label: "Hub erreichbar (/health)",
		state: health.value?.ok ? "ok" : "fail",
		detail: health.value ? `HTTP ${health.value.status}` : health.timedOut ? "keine Antwort in 8 s" : describeError(health.error),
	});
	checks.push({
		id: "hub-token",
		group: "Hub",
		label: "Hub-Token auf diesem Gerät",
		state: deps.token ? "ok" : "fail",
		detail: deps.token ? "gesetzt" : "fehlt: Einstellungen → Verbindung",
	});
	let engines: ListenEngineLite[] = [];
	const res = await settle(deps.hubFetch("/v1/listen/engines"), 8000);
	if (!res.value) {
		checks.push({ id: "listen-engines", group: "Hub", label: "/v1/listen/engines", state: "fail", detail: res.timedOut ? "keine Antwort in 8 s" : describeError(res.error) });
		return { checks, engines };
	}
	const body = await res.value.text().catch(() => "");
	let parsed: { engines?: { name?: unknown; kind?: unknown; private?: unknown; models?: unknown }[]; default?: unknown } = {};
	try {
		parsed = JSON.parse(body) as typeof parsed;
	}
	catch {
		// reported below via the raw status
	}
	engines = (Array.isArray(parsed.engines) ? parsed.engines : []).map(e => ({
		name: String(e?.name ?? ""),
		kind: String(e?.kind ?? ""),
		private: e?.private !== false,
	}));
	const liveDisabled = deps.liveSettings.source === "hub" && engines.length === 0;
	checks.push({
		id: "listen-engines",
		group: "Hub",
		label: "/v1/listen/engines",
		state: !res.value.ok || liveDisabled ? "fail" : "ok",
		detail: !res.value.ok
			? `HTTP ${res.value.status}: ${body.slice(0, 160)}`
			: engines.length
				? `${engines.map(e => `${e.name} (${e.kind}${e.private ? "" : ", Cloud"})`).join(", ")} · Standard ${String(parsed.default ?? "keiner")}`
				: "keine Engine: Live-Start ist deaktiviert",
		data: { status: res.value.status, engines, default: parsed.default ?? null },
	});
	return { checks, engines };
}

interface SocketMessage { atMs: number; kind: "text" | "binary"; type: string | null; isFinal: boolean | null; transcriptChars: number | null; description: string | null }

/** The live page's WebSocket (same URL params), fed with the captured PCM or 1 s of silence. */
async function socketCheck(deps: DiagnoseDeps, engines: ListenEngineLite[], pcm: Int16Array[]): Promise<ProbeCheck> {
	const base = { id: "listen-socket", group: "Hub", label: "WebSocket /v1/listen" };
	const WS = g().WebSocket as typeof WebSocket | undefined;
	if (!WS) {
		return { ...base, state: "fail", detail: "WebSocket fehlt" };
	}
	const url = new URL(`${toWsUrl(deps.hubUrl)}/v1/listen`);
	url.searchParams.set("encoding", "linear16");
	url.searchParams.set("sample_rate", "16000");
	url.searchParams.set("interim_results", "true");
	url.searchParams.set("language", deps.language);
	if (deps.liveSettings.hubEngine) url.searchParams.set("engine", deps.liveSettings.hubEngine);
	const selected = engines.find(e => e.name === deps.liveSettings.hubEngine);
	if (selected && !selected.private) url.searchParams.set("private", "false");
	const shownUrl = url.toString();
	url.searchParams.set("token", deps.token);

	const audio = pcm.length ? pcm : [new Int16Array(16000)];
	const messages: SocketMessage[] = [];
	let messageCount = 0;
	let openedMs: number | null = null;
	let errored = false;
	const t0 = performance.now();
	const at = () => Math.round(performance.now() - t0);

	const outcome = await new Promise<{ code: number | null; reason: string; wasClean: boolean | null; closedMs: number | null; timedOut: boolean; ctorError?: string }>((resolve) => {
		let sock: WebSocket;
		try {
			sock = new WS(url.toString());
		}
		catch (err) {
			resolve({ code: null, reason: "", wasClean: null, closedMs: null, timedOut: false, ctorError: describeError(err) });
			return;
		}
		sock.binaryType = "arraybuffer";
		const timer = setTimeout(() => {
			try {
				sock.close();
			}
			catch {
				// ignore
			}
			resolve({ code: null, reason: "", wasClean: null, closedMs: null, timedOut: true });
		}, SOCKET_TIMEOUT_MS);
		sock.onopen = () => {
			openedMs = at();
			// Fresh arrays from floatTo16kPCM: the buffer is exactly the frame.
			for (const frame of audio) sock.send(frame.buffer as ArrayBuffer);
			setTimeout(() => {
				if (sock.readyState === WS.OPEN) sock.send(JSON.stringify({ type: "CloseStream" }));
			}, 1500);
		};
		sock.onmessage = (e: MessageEvent) => {
			messageCount += 1;
			if (messages.length >= 6) return;
			if (typeof e.data !== "string") {
				messages.push({ atMs: at(), kind: "binary", type: null, isFinal: null, transcriptChars: null, description: null });
				return;
			}
			let frame: { type?: unknown; is_final?: unknown; description?: unknown; channel?: { alternatives?: { transcript?: unknown }[] } } = {};
			try {
				frame = JSON.parse(e.data) as typeof frame;
			}
			catch {
				// non-JSON frame: recorded with type null
			}
			const transcript = frame.channel?.alternatives?.[0]?.transcript;
			messages.push({
				atMs: at(),
				kind: "text",
				type: typeof frame.type === "string" ? frame.type : null,
				isFinal: typeof frame.is_final === "boolean" ? frame.is_final : null,
				// Length only: dictated text never enters the report.
				transcriptChars: typeof transcript === "string" ? transcript.length : null,
				description: typeof frame.description === "string" ? frame.description.slice(0, 200) : null,
			});
		};
		sock.onerror = () => {
			errored = true;
		};
		sock.onclose = (e: CloseEvent) => {
			clearTimeout(timer);
			resolve({ code: e.code, reason: e.reason.slice(0, 200), wasClean: e.wasClean, closedMs: at(), timedOut: false });
		};
	});

	const first = messages[0];
	const errorFrame = messages.find(m => m.type === "Error");
	const results = messages.filter(m => m.type === "Results").length;
	let state: ProbeState = "ok";
	let detail: string;
	if (outcome.ctorError) {
		state = "fail";
		detail = `Konstruktor: ${outcome.ctorError}`;
	}
	else if (openedMs === null) {
		state = "fail";
		detail = outcome.timedOut
			? "nicht geöffnet in 10 s"
			: `nicht geöffnet: close ${outcome.code ?? "?"}${outcome.reason ? ` „${outcome.reason}“` : ""} (Token/CORS/Proxy?)`;
	}
	else {
		detail = `offen nach ${openedMs} ms · ${audio.length} Audio-Frames (${pcm.length ? "Mikrofon" : "Stille"}) gesendet · ${messageCount} Nachrichten`;
		if (first) detail += ` · erste: ${first.type ?? first.kind}`;
		if (errorFrame) {
			state = "fail";
			detail += ` · Error: ${errorFrame.description ?? "?"}`;
		}
		else if (messageCount === 0) {
			state = "warn";
			detail += " · keine Antwort vom Hub";
		}
		detail += outcome.timedOut ? " · nicht geschlossen in 10 s" : ` · close ${outcome.code}${outcome.reason ? ` „${outcome.reason}“` : ""}`;
	}
	return {
		...base,
		state,
		detail,
		data: { url: shownUrl, openedMs, errored, messageCount, results, messages, ...outcome },
	};
}

// ── On-device AI ──────────────────────────────────────────────────────────

async function webSpeechCheck(language: string): Promise<ProbeCheck> {
	const base = { id: "web-speech", group: "Gerät-KI", label: "Web Speech (lokale Spracherkennung)" };
	const ctorName = g().SpeechRecognition ? "SpeechRecognition" : g().webkitSpeechRecognition ? "webkitSpeechRecognition" : null;
	if (!ctorName) {
		return { ...base, state: "warn", detail: "nicht vorhanden: Quelle „Gerät“ unmöglich" };
	}
	const Ctor = g()[ctorName] as { available?: (o: unknown) => Promise<string>; availability?: (o: unknown) => Promise<string>; install?: unknown; new (): AnyRecord };
	const lang = language === "de" ? "de-DE" : language === "en" ? "en-US" : language;
	const opts = { langs: [lang], processLocally: true };
	let processLocally: boolean | null = null;
	try {
		processLocally = "processLocally" in new Ctor();
	}
	catch {
		// constructor may throw outside a gesture on some engines
	}
	const fn = typeof Ctor.available === "function" ? "available" : typeof Ctor.availability === "function" ? "availability" : null;
	let result: string | null = null;
	let error: string | null = null;
	if (fn) {
		const res = await settle((Ctor[fn] as (o: unknown) => Promise<string>).call(Ctor, opts), 5000);
		result = res.value ?? null;
		error = res.timedOut ? "hängt >5 s" : res.error ? describeError(res.error) : null;
	}
	const data = { ctor: ctorName, staticAvailability: fn, install: typeof Ctor.install === "function", processLocally, result, error };
	if (!fn) {
		return { ...base, state: "warn", detail: `${ctorName} ohne available(): nur Cloud-Erkennung, die die App nicht nutzt`, data };
	}
	return {
		...base,
		state: result === "available" ? "ok" : "warn",
		detail: `${ctorName}.${fn}(${lang}, lokal) = ${result ?? error ?? "?"}${data.install ? " · install() vorhanden (nicht ausgelöst)" : ""}`,
		data,
	};
}

async function promptApiCheck(language: string): Promise<ProbeCheck[]> {
	const out: ProbeCheck[] = [];
	const LM = g().LanguageModel as { availability?: (o?: unknown) => Promise<string>; params?: () => Promise<unknown> } | undefined;
	const base = { id: "prompt-api", group: "Gerät-KI", label: "Prompt API (LanguageModel, für Titelvorschlag)" };
	if (!LM?.availability) {
		out.push({ ...base, state: "warn", detail: "LanguageModel nicht vorhanden (nur Chrome Desktop mit eingebautem Modell)" });
	}
	else {
		const opts = { expectedInputs: [{ type: "text", languages: [language] }], expectedOutputs: [{ type: "text", languages: [language] }] };
		const withLang = await settle(LM.availability(opts), 5000);
		const plain = await settle(LM.availability(), 5000);
		const params = LM.params ? await settle(LM.params(), 5000) : null;
		const fmt = (r: { value?: string; error?: unknown; timedOut: boolean }) => r.value ?? (r.timedOut ? "hängt" : describeError(r.error));
		out.push({
			...base,
			state: withLang.value === "available" ? "ok" : "warn",
			detail: `availability(${language}) = ${fmt(withLang)} · ohne Sprache = ${fmt(plain)} (Download nicht ausgelöst)`,
			data: { withLanguage: fmt(withLang), plain: fmt(plain), params: params?.value ?? null },
		});
	}
	const Summ = g().Summarizer as { availability?: (o?: unknown) => Promise<string> } | undefined;
	if (Summ?.availability) {
		const res = await settle(Summ.availability({ type: "headline", format: "plain-text", length: "short", expectedInputLanguages: [language], outputLanguage: language }), 5000);
		out.push({
			id: "summarizer-api",
			group: "Gerät-KI",
			label: "Summarizer API (headline)",
			state: res.value === "available" ? "ok" : "info",
			detail: res.value ?? (res.timedOut ? "hängt" : describeError(res.error)),
		});
	}
	else {
		out.push({ id: "summarizer-api", group: "Gerät-KI", label: "Summarizer API (headline)", state: "info", detail: "nicht vorhanden" });
	}
	const ai = g().ai as AnyRecord | undefined;
	out.push({
		id: "window-ai",
		group: "Gerät-KI",
		label: "window.ai (alte Vorschau-API)",
		state: "info",
		detail: ai ? `vorhanden: ${Object.keys(ai).join(", ") || "(leer)"}` : "nicht vorhanden",
	});
	return out;
}

// ── Orchestration ─────────────────────────────────────────────────────────

/**
 * Run every check. MUST be called synchronously from a click handler: the
 * first statement creates the comparison AudioContext inside the gesture.
 */
export function runDiagnose(deps: DiagnoseDeps): Promise<DebugReport> {
	const Ctor = audioContextCtor();
	let gestureCtx: AudioContext | null = null;
	let gestureState = "fehlt";
	try {
		if (Ctor) {
			gestureCtx = new Ctor();
			gestureState = gestureCtx.state;
		}
	}
	catch (err) {
		gestureState = `Fehler: ${describeError(err)}`;
	}
	return diagnose(deps, gestureCtx, gestureState);
}

async function diagnose(deps: DiagnoseDeps, gestureCtx: AudioContext | null, gestureState: string): Promise<DebugReport> {
	const started = performance.now();
	const checks: ProbeCheck[] = [];
	const push = (c: ProbeCheck) => {
		checks.push(c);
		deps.onCheck?.(c);
	};
	for (const c of deviceChecks()) push(c);
	push(await wakeLockCheck());
	push(await micPermissionCheck());
	push(mimeCheck());
	push({
		id: "audio-context-gesture",
		group: "Audio",
		label: "AudioContext direkt in der Nutzergeste",
		state: gestureState === "running" ? "ok" : gestureState === "suspended" ? "warn" : "fail",
		detail: gestureState,
	});
	const pcm = await audioChecks(gestureCtx, push);
	const hub = await hubChecks(deps);
	for (const c of hub.checks) push(c);
	push(await socketCheck(deps, hub.engines, pcm));
	push(await webSpeechCheck(deps.language));
	for (const c of await promptApiCheck(deps.language)) push(c);
	return {
		version: 1,
		createdAt: new Date().toISOString(),
		durationMs: Math.round(performance.now() - started),
		context: { ...deps.context, language: deps.language, liveSettings: { ...deps.liveSettings }, hubUrl: deps.hubUrl },
		checks,
		pttLog: deps.pttLog ?? [],
	};
}

/**
 * The JSON body for `POST /api/debug-report`: redacted (`secrets` = the device
 * token and anything else that must not leave the phone) and shrunk to fit the
 * route's size limit (drops per-check `data`, then trims details).
 */
export function buildDebugPayload(report: DebugReport, secrets: readonly string[]): string {
	const redacted = redactDebugReport(report, secrets);
	let body = JSON.stringify(redacted);
	if (byteLength(body) <= DEBUG_REPORT_MAX_BYTES) return body;
	const lean = { ...redacted, truncated: true, checks: redacted.checks.map(c => ({ ...c, data: undefined })) };
	body = JSON.stringify(lean);
	if (byteLength(body) <= DEBUG_REPORT_MAX_BYTES) return body;
	const minimal = {
		...lean,
		pttLog: lean.pttLog.slice(0, 5),
		checks: lean.checks.map(c => ({ ...c, detail: c.detail.slice(0, 300) })),
	};
	return JSON.stringify(minimal);
}
