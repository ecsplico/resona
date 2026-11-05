import { reactive, watch } from "vue";

/**
 * Persisted live-transcription preferences: which source feeds the live view
 * (hub stream or on-device Web Speech), the hub listen engine, and the chunked
 * correction options. Stored in localStorage like `useCalendarSettings` and
 * exposed as a module singleton so the settings drawer and the live page share
 * one reactive object.
 */
export interface LiveSettings {
	source: "hub" | "device";
	/** Hub listen engine; empty → the hub's default. */
	hubEngine: string;
	correction: boolean;
	correctionEngine: string;
	/** Correction chunk length in seconds, 8–30. */
	chunkSeconds: number;
}

const STORAGE_KEY = "resona.live.settings";

export const CHUNK_SECONDS_MIN = 8;
export const CHUNK_SECONDS_MAX = 30;

const DEFAULTS: LiveSettings = {
	source: "hub",
	hubEngine: "",
	correction: false,
	correctionEngine: "",
	chunkSeconds: 15,
};

export function clampChunkSeconds(value: number): number {
	if (!Number.isFinite(value)) {
		return DEFAULTS.chunkSeconds;
	}
	return Math.max(CHUNK_SECONDS_MIN, Math.min(CHUNK_SECONDS_MAX, Math.trunc(value)));
}

let singleton: LiveSettings | null = null;

function load(target: LiveSettings): void {
	if (typeof window === "undefined") {
		return;
	}
	try {
		const raw = window.localStorage.getItem(STORAGE_KEY);
		if (!raw) {
			return;
		}
		const saved = JSON.parse(raw) as Partial<LiveSettings>;
		if (saved.source === "hub" || saved.source === "device") {
			target.source = saved.source;
		}
		if (typeof saved.hubEngine === "string") {
			target.hubEngine = saved.hubEngine;
		}
		if (typeof saved.correction === "boolean") {
			target.correction = saved.correction;
		}
		if (typeof saved.correctionEngine === "string") {
			target.correctionEngine = saved.correctionEngine;
		}
		if (typeof saved.chunkSeconds === "number") {
			target.chunkSeconds = clampChunkSeconds(saved.chunkSeconds);
		}
	}
	catch {
		// Corrupt/blocked storage — fall back to defaults.
	}
}

export function useLiveSettings(): LiveSettings {
	if (!singleton) {
		const state = reactive<LiveSettings>({ ...DEFAULTS });
		load(state);
		if (typeof window !== "undefined") {
			watch(state, (value) => {
				try {
					window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
				}
				catch {
					// Ignore write failures (private mode / quota).
				}
			}, { deep: true });
		}
		singleton = state;
	}
	return singleton;
}
