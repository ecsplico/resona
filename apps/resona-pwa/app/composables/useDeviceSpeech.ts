import { computed, getCurrentScope, onScopeDispose, ref } from "vue";
import type { ComputedRef, Ref } from "vue";
import { Capacitor, type PluginListenerHandle } from "@capacitor/core";
import type { PcmCapture } from "~/composables/usePcmCapture";
import { pcmToWav } from "~/utils/pcm";
import { startRecordingService, stopRecordingService } from "~/utils/recordingService";
import { decodePcm, hasResonaSpeech, ResonaSpeech, RESONA_SPEECH_SAMPLE_RATE, type ResonaSpeechError, type ResonaSpeechUpdate } from "~/utils/resonaSpeech";

/**
 * On-device live transcription, never in the cloud. Two paths:
 * - App shell with the `ResonaSpeech` plugin (Android 13+ and iOS, `~/utils/resonaSpeech`):
 *   the plugin owns the microphone, its recognizer gives the preview (committed + tentative)
 *   and its 16 kHz PCM is exposed as `pcm` (a `PcmCapture` for `useLiveCorrection`) and,
 *   with `keepAudio`, kept for `recording()`. Goes on with the screen locked: on Android
 *   under the microphone foreground service, on iOS through the plugin's audio session and
 *   the app's `audio` background mode.
 * - Elsewhere the Web Speech API restricted to local processing (`processLocally = true`).
 * Mirrors the `useLive` surface so the live page can swap sources.
 */
export type DeviceSpeechAvailability = "unsupported" | "unavailable" | "downloadable" | "downloading" | "available";

// Minimal Web Speech typings: the DOM lib lacks `processLocally`, the static
// `available`/`install`, and isn't guaranteed to ship the rest at all.
interface SpeechAlternativeLike { transcript: string }
interface SpeechResultLike { readonly isFinal: boolean; readonly length: number; [index: number]: SpeechAlternativeLike }
interface SpeechResultEventLike { resultIndex: number; results: { readonly length: number; [index: number]: SpeechResultLike } }
interface SpeechErrorEventLike { error: string }
interface SpeechOptions { langs: string[]; processLocally: boolean }

interface SpeechRecognizer {
	lang: string;
	continuous: boolean;
	interimResults: boolean;
	processLocally: boolean;
	onresult: ((event: SpeechResultEventLike) => void) | null;
	onerror: ((event: SpeechErrorEventLike) => void) | null;
	onend: (() => void) | null;
	start(): void;
	stop(): void;
	abort?(): void;
}

interface SpeechRecognitionCtor {
	new (): SpeechRecognizer;
	available?(options: SpeechOptions): Promise<Exclude<DeviceSpeechAvailability, "unsupported">>;
	install?(options: SpeechOptions): Promise<boolean>;
}

const ERROR_MESSAGES: Record<string, string> = {
	"not-allowed": "Mikrofon nicht erlaubt",
	"service-not-allowed": "Lokale Spracherkennung nicht erlaubt",
	"language-not-supported": "Sprache lokal nicht verfügbar",
	"language-unavailable": "Sprachpaket für die lokale Erkennung fehlt",
	"audio-capture": "Kein Mikrofon",
};

/**
 * Why local recognition can't start, per availability state. `unsupported`: neither the
 * Resona app (Android 13+ or iOS, `ResonaSpeech`) nor a browser with on-device Web Speech
 * (`available()`/`processLocally`), which today only desktop Chrome ≥ 139 ships; Safari on
 * iOS has no such API and Chrome on Android exposes it but always answers `unavailable`.
 */
export const DEVICE_SPEECH_HINTS: Record<Exclude<DeviceSpeechAvailability, "available">, string> = {
	unsupported: "Dieses Gerät kann nicht lokal erkennen (nur die Resona-App für Android 13+ und iPhone oder Chrome am Desktop, nicht Safari)",
	unavailable: "Lokale Erkennung hier nicht verfügbar (z. B. Chrome auf Android oder Sprache nicht unterstützt)",
	downloadable: "Sprachpaket fehlt: Einstellungen → Live → Sprachpaket laden",
	downloading: "Sprachpaket wird noch geladen",
};

/** Engine stamp of a live transcript from this source; the plugin's per app platform. */
export const DEVICE_SPEECH_ENGINE = { web: "web-speech", android: "android-speech", ios: "ios-speech" } as const;

export function deviceSpeechEngine(native: boolean): string {
	if (!native) return DEVICE_SPEECH_ENGINE.web;
	return Capacitor.getPlatform() === "ios" ? DEVICE_SPEECH_ENGINE.ios : DEVICE_SPEECH_ENGINE.android;
}

export function bcp47(language: string): string {
	if (language === "de") return "de-DE";
	if (language === "en") return "en-US";
	return language;
}

function recognitionCtor(): SpeechRecognitionCtor | null {
	const g = globalThis as unknown as { SpeechRecognition?: SpeechRecognitionCtor; webkitSpeechRecognition?: SpeechRecognitionCtor };
	return g.SpeechRecognition ?? g.webkitSpeechRecognition ?? null;
}

function speechOptions(language: string): SpeechOptions {
	return { langs: [bcp47(language)], processLocally: true };
}

export interface DeviceSpeechStartOptions {
	language: string;
	/** Native only: keep the session's PCM for `recording()` (the saved audio). */
	keepAudio?: boolean;
}

export interface DeviceRecording {
	blob: Blob;
	seconds: number;
}

export interface DeviceSpeech {
	/** True when the `ResonaSpeech` plugin runs the session (it then owns the microphone). */
	native: boolean;
	partial: Ref<string>;
	final: Ref<string>;
	fullText: ComputedRef<string>;
	status: Ref<"OPEN" | "CLOSED">;
	error: Ref<string | null>;
	/** Native: the session's microphone audio as 16 kHz PCM frames. Web: never emits. */
	pcm: PcmCapture;
	start: (opts: DeviceSpeechStartOptions) => Promise<void>;
	stop: () => void;
	/**
	 * Ends the session and waits until it is over: native, after the last PCM frame and
	 * with the trailing hypothesis committed (reported through `onFinal`).
	 */
	finish: () => Promise<void>;
	onFinal: (cb: (text: string) => void) => () => void;
	availability: (language: string) => Promise<DeviceSpeechAvailability>;
	install: (language: string) => Promise<boolean>;
	/** Native with `keepAudio`: the last session's audio as WAV, else null. */
	recording: () => DeviceRecording | null;
}

export function useDeviceSpeech(): DeviceSpeech {
	const native = hasResonaSpeech();
	const partial = ref("");
	const final = ref("");
	const status = ref<"OPEN" | "CLOSED">("CLOSED");
	const error = ref<string | null>(null);
	const fullText: ComputedRef<string> = computed(() =>
		partial.value ? `${final.value} ${partial.value}`.trim() : final.value,
	);

	const finalListeners = new Set<(text: string) => void>();
	const pcmListeners = new Set<(pcm: Int16Array) => void>();
	let recognizer: SpeechRecognizer | null = null;
	let stopped = true;

	const pcm: PcmCapture = {
		sampleRate: RESONA_SPEECH_SAMPLE_RATE,
		// The plugin opens and closes the microphone with the session.
		async start() {},
		stop() {},
		onFrame(cb) {
			pcmListeners.add(cb);
			return () => {
				pcmListeners.delete(cb);
			};
		},
	};

	function onFinal(cb: (text: string) => void): () => void {
		finalListeners.add(cb);
		return () => finalListeners.delete(cb);
	}

	function emitFinal(text: string): void {
		for (const cb of finalListeners) cb(text);
	}

	async function availability(language: string): Promise<DeviceSpeechAvailability> {
		if (native) {
			return (await ResonaSpeech.available({ language: bcp47(language) })).status;
		}
		const Ctor = recognitionCtor();
		if (!Ctor || typeof Ctor.available !== "function") {
			return "unsupported";
		}
		return await Ctor.available(speechOptions(language));
	}

	async function install(language: string): Promise<boolean> {
		if (native) {
			return (await ResonaSpeech.install({ language: bcp47(language) })).started;
		}
		const Ctor = recognitionCtor();
		if (!Ctor || typeof Ctor.install !== "function") {
			return false;
		}
		return await Ctor.install(speechOptions(language));
	}

	// --- native (ResonaSpeech) -------------------------------------------------------

	let nativeSession = 0;
	let nativeRevision = -1;
	let nativeHandles: PluginListenerHandle[] = [];
	let nativeEnding: Promise<void> | null = null;
	let keepAudio = false;
	let audioChunks: Int16Array[] = [];
	let audioSamples = 0;

	/** Committed text only grows: what is new since the last update goes to `onFinal`. */
	function applyCommitted(committed: string): void {
		const text = committed.trim();
		if (text === final.value) return;
		const added = text.startsWith(final.value) ? text.slice(final.value.length).trim() : text;
		final.value = text;
		if (added) emitFinal(added);
	}

	function onNativeUpdate(session: number, event: ResonaSpeechUpdate): void {
		if (session !== nativeSession || event.revision <= nativeRevision) return;
		nativeRevision = event.revision;
		applyCommitted(event.committed);
		partial.value = event.tentative.trim();
	}

	function onNativePcm(session: number, data: string): void {
		if (session !== nativeSession) return;
		const samples = decodePcm(data);
		if (samples.length === 0) return;
		if (keepAudio) {
			audioChunks.push(samples);
			audioSamples += samples.length;
		}
		for (const cb of pcmListeners) cb(samples);
	}

	function onNativeError(session: number, event: ResonaSpeechError): void {
		if (session !== nativeSession) return;
		error.value = ERROR_MESSAGES[event.code] ?? event.message;
		if (event.fatal) {
			// The microphone stopped: nothing more comes; release the plugin and the service.
			void finish();
		}
	}

	async function releaseNative(): Promise<void> {
		const handles = nativeHandles;
		nativeHandles = [];
		await Promise.all(handles.map(h => h.remove().catch(() => undefined)));
		await stopRecordingService();
	}

	async function startNative(opts: DeviceSpeechStartOptions): Promise<void> {
		await finish();
		const session = ++nativeSession;
		nativeRevision = -1;
		keepAudio = opts.keepAudio === true;
		audioChunks = [];
		audioSamples = 0;
		final.value = "";
		partial.value = "";
		error.value = null;
		stopped = false;
		// Before the microphone opens: Android cuts the capture of a locked phone otherwise.
		await startRecordingService().catch(() => undefined);
		nativeHandles = await Promise.all([
			ResonaSpeech.addListener("update", e => onNativeUpdate(session, e)),
			ResonaSpeech.addListener("pcm", e => onNativePcm(session, e.data)),
			ResonaSpeech.addListener("error", e => onNativeError(session, e)),
		]);
		try {
			await ResonaSpeech.start({ language: bcp47(opts.language), emitPcm: true });
		}
		catch (err) {
			stopped = true;
			nativeSession += 1;
			await releaseNative();
			const code = err && typeof err === "object" && "code" in err && typeof err.code === "string" ? err.code : undefined;
			throw new Error((code && ERROR_MESSAGES[code]) || (err instanceof Error && err.message) || "Lokale Spracherkennung nicht verfügbar", { cause: err });
		}
		if (session === nativeSession && !stopped) {
			status.value = "OPEN";
		}
	}

	function finishNative(): Promise<void> {
		if (nativeEnding) return nativeEnding;
		if (stopped) return Promise.resolve();
		stopped = true;
		status.value = "CLOSED";
		const session = nativeSession;
		nativeEnding = (async () => {
			try {
				const { text } = await ResonaSpeech.stop().catch(() => ({ text: fullText.value }));
				if (session === nativeSession) {
					// The plugin's text holds the trailing hypothesis as committed.
					applyCommitted(text || fullText.value);
					partial.value = "";
				}
			}
			finally {
				nativeSession += 1;
				await releaseNative();
				nativeEnding = null;
			}
		})();
		return nativeEnding;
	}

	function recording(): DeviceRecording | null {
		if (audioSamples === 0) return null;
		const samples = new Int16Array(audioSamples);
		let offset = 0;
		for (const chunk of audioChunks) {
			samples.set(chunk, offset);
			offset += chunk.length;
		}
		return { blob: pcmToWav(samples, RESONA_SPEECH_SAMPLE_RATE), seconds: audioSamples / RESONA_SPEECH_SAMPLE_RATE };
	}

	// --- Web Speech ------------------------------------------------------------------

	function handleResult(event: SpeechResultEventLike): void {
		let interim = "";
		for (let i = event.resultIndex; i < event.results.length; i++) {
			const result = event.results[i]!;
			const transcript = (result[0]?.transcript ?? "").trim();
			if (result.isFinal) {
				if (!transcript) continue;
				final.value = final.value ? `${final.value} ${transcript}` : transcript;
				emitFinal(transcript);
			}
			else {
				interim = interim ? `${interim} ${transcript}` : transcript;
			}
		}
		partial.value = interim;
	}

	async function startWeb(opts: DeviceSpeechStartOptions): Promise<void> {
		const state = await availability(opts.language);
		if (state !== "available") {
			throw new Error(DEVICE_SPEECH_HINTS[state]);
		}
		stopWeb();
		const Ctor = recognitionCtor()!;
		const rec = new Ctor();
		rec.lang = bcp47(opts.language);
		rec.continuous = true;
		rec.interimResults = true;
		rec.processLocally = true;
		rec.onresult = handleResult;
		rec.onerror = (event) => {
			if (event.error === "no-speech") return;
			const message = ERROR_MESSAGES[event.error];
			if (message) {
				error.value = message;
				stopWeb();
			}
		};
		rec.onend = () => {
			if (stopped || recognizer !== rec) return;
			try {
				rec.start();
			}
			catch {
				stopWeb();
			}
		};

		final.value = "";
		partial.value = "";
		error.value = null;
		stopped = false;
		recognizer = rec;
		rec.start();
		status.value = "OPEN";
	}

	function stopWeb(): void {
		stopped = true;
		const rec = recognizer;
		recognizer = null;
		status.value = "CLOSED";
		partial.value = "";
		if (rec) {
			try {
				rec.stop();
			}
			catch {
				// Already stopped.
			}
		}
	}

	// --- shared surface --------------------------------------------------------------

	async function start(opts: DeviceSpeechStartOptions): Promise<void> {
		await (native ? startNative(opts) : startWeb(opts));
	}

	function finish(): Promise<void> {
		if (native) return finishNative();
		stopWeb();
		return Promise.resolve();
	}

	function stop(): void {
		void finish();
	}

	if (getCurrentScope()) {
		onScopeDispose(stop);
	}

	return { native, partial, final, fullText, status, error, pcm, start, stop, finish, onFinal, availability, install, recording };
}
