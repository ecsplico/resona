import { Capacitor, registerPlugin, WebPlugin } from "@capacitor/core";
import type { PluginListenerHandle } from "@capacitor/core";

/**
 * App-local Capacitor plugin `ResonaSpeech` (Android:
 * `android/app/src/main/java/de/ecsplico/resona/ResonaSpeechPlugin.java`, Android 13+):
 * on-device live recognition as a preview plus the same microphone audio as 16 kHz PCM.
 * The app captures the audio itself (`AudioRecord`) and feeds the on-device recognizer
 * through `EXTRA_AUDIO_SOURCE`, so it keeps running with the screen locked under the
 * microphone foreground service (`~/utils/recordingService`).
 *
 * Events: `update` (committed text never changes again, tentative text is the segment in
 * progress, `revision` grows with every change), `pcm` (base64 little-endian int16, 16 kHz
 * mono, 250 ms each), `error` (`fatal`: the microphone stopped; otherwise only the preview
 * gave up while the audio keeps coming). `stop()` resolves after the last `pcm` event with
 * the whole on-device text.
 */
export type ResonaSpeechStatus = "unsupported" | "unavailable" | "downloadable" | "downloading" | "available";

export interface ResonaSpeechUpdate {
	committed: string;
	tentative: string;
	revision: number;
}

export interface ResonaSpeechPcm {
	data: string;
}

export interface ResonaSpeechError {
	code: string;
	message: string;
	fatal: boolean;
}

export interface ResonaSpeechPlugin {
	available(options: { language: string }): Promise<{ status: ResonaSpeechStatus }>;
	install(options: { language: string }): Promise<{ started: boolean }>;
	start(options: { language: string; emitPcm: boolean }): Promise<void>;
	stop(): Promise<{ text: string }>;
	addListener(event: "update", listener: (event: ResonaSpeechUpdate) => void): Promise<PluginListenerHandle>;
	addListener(event: "pcm", listener: (event: ResonaSpeechPcm) => void): Promise<PluginListenerHandle>;
	addListener(event: "error", listener: (event: ResonaSpeechError) => void): Promise<PluginListenerHandle>;
	removeAllListeners(): Promise<void>;
}

export const RESONA_SPEECH_SAMPLE_RATE = 16000;

/**
 * Web fallback: the browser has no such plugin; `useDeviceSpeech` uses Web Speech there and
 * only asks this when the native plugin is compiled in. Answers like a device without it.
 */
export class ResonaSpeechWeb extends WebPlugin implements ResonaSpeechPlugin {
	async available(_options: { language: string }): Promise<{ status: ResonaSpeechStatus }> {
		return { status: "unsupported" };
	}

	async install(_options: { language: string }): Promise<{ started: boolean }> {
		return { started: false };
	}

	async start(_options: { language: string; emitPcm: boolean }): Promise<void> {
		throw this.unavailable("ResonaSpeech needs the Android app.");
	}

	async stop(): Promise<{ text: string }> {
		return { text: "" };
	}
}

export const ResonaSpeech = registerPlugin<ResonaSpeechPlugin>("ResonaSpeech", {
	web: () => new ResonaSpeechWeb(),
});

/** True inside an app shell that compiles the plugin in (today: Android). */
export function hasResonaSpeech(): boolean {
	return Capacitor.isNativePlatform() && Capacitor.isPluginAvailable("ResonaSpeech");
}

/** One `pcm` event's payload → samples (the plugin sends little-endian, as every app CPU is). */
export function decodePcm(data: string): Int16Array {
	const binary = atob(data);
	const bytes = new Uint8Array(binary.length - (binary.length % 2));
	for (let i = 0; i < bytes.length; i++) bytes[i] = binary.charCodeAt(i);
	return new Int16Array(bytes.buffer);
}
