import { computed, getCurrentScope, onScopeDispose, ref } from "vue";
import type { ComputedRef, Ref } from "vue";

/**
 * On-device live transcription via the Web Speech API, restricted to local
 * processing (`processLocally = true`) — cloud browser recognition is never
 * used. Mirrors the `useLive` surface so the live page can swap sources.
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
	"audio-capture": "Kein Mikrofon",
};

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

export function useDeviceSpeech(): {
	partial: Ref<string>;
	final: Ref<string>;
	fullText: ComputedRef<string>;
	status: Ref<"OPEN" | "CLOSED">;
	error: Ref<string | null>;
	start: (opts: { language: string }) => Promise<void>;
	stop: () => void;
	onFinal: (cb: (text: string) => void) => () => void;
	availability: (language: string) => Promise<DeviceSpeechAvailability>;
	install: (language: string) => Promise<boolean>;
} {
	const partial = ref("");
	const final = ref("");
	const status = ref<"OPEN" | "CLOSED">("CLOSED");
	const error = ref<string | null>(null);
	const fullText: ComputedRef<string> = computed(() =>
		partial.value ? `${final.value} ${partial.value}`.trim() : final.value,
	);

	const finalListeners = new Set<(text: string) => void>();
	let recognizer: SpeechRecognizer | null = null;
	let stopped = true;

	function onFinal(cb: (text: string) => void): () => void {
		finalListeners.add(cb);
		return () => finalListeners.delete(cb);
	}

	async function availability(language: string): Promise<DeviceSpeechAvailability> {
		const Ctor = recognitionCtor();
		if (!Ctor || typeof Ctor.available !== "function") {
			return "unsupported";
		}
		return await Ctor.available(speechOptions(language));
	}

	async function install(language: string): Promise<boolean> {
		const Ctor = recognitionCtor();
		if (!Ctor || typeof Ctor.install !== "function") {
			return false;
		}
		return await Ctor.install(speechOptions(language));
	}

	function handleResult(event: SpeechResultEventLike): void {
		let interim = "";
		for (let i = event.resultIndex; i < event.results.length; i++) {
			const result = event.results[i]!;
			const transcript = (result[0]?.transcript ?? "").trim();
			if (result.isFinal) {
				if (!transcript) continue;
				final.value = final.value ? `${final.value} ${transcript}` : transcript;
				for (const cb of finalListeners) cb(transcript);
			}
			else {
				interim = interim ? `${interim} ${transcript}` : transcript;
			}
		}
		partial.value = interim;
	}

	async function start(opts: { language: string }): Promise<void> {
		const state = await availability(opts.language);
		if (state !== "available") {
			throw new Error(`Lokale Spracherkennung nicht bereit (${state})`);
		}
		stop();
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
				stop();
			}
		};
		rec.onend = () => {
			if (stopped || recognizer !== rec) return;
			try {
				rec.start();
			}
			catch {
				stop();
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

	function stop(): void {
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

	if (getCurrentScope()) {
		onScopeDispose(stop);
	}

	return { partial, final, fullText, status, error, start, stop, onFinal, availability, install };
}
