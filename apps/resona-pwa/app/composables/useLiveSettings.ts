import { reactive, watch } from "vue";
import type { OrgLiveDefaults } from "~/composables/useHub";

/**
 * Persisted live-transcription preferences: which source feeds the live view
 * (hub stream or on-device Web Speech), the hub listen engine, the chunked
 * correction options and the cleanup pass. Stored in localStorage like
 * `useCalendarSettings` and exposed as a module singleton so the settings drawer
 * and the live page share one reactive object. Empty engine values inherit the
 * organization's `live` defaults (`effectiveLiveSettings`).
 */
export interface LiveSettings {
	source: "hub" | "device";
	/** Hub listen engine; empty → the organization's `live.engine`, else the hub's default. */
	hubEngine: string;
	correction: boolean;
	/** Correction engine; empty → the organization's `live.correction_execution_profile`, else the hub's default. */
	correctionEngine: string;
	/** Correction chunk length in seconds, 8–30. */
	chunkSeconds: number;
	/** Run the organization's `live.cleanup_profile` on the finished live text. */
	cleanup: boolean;
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
	cleanup: false,
};

/** What a live session uses: device values, empty ones filled from the organization's `live` defaults. */
export interface EffectiveLiveSettings {
	/** Listen engine; empty → the hub picks (its default is the org's `live.engine` when streamable). */
	hubEngine: string;
	/** True when `hubEngine` comes from the organization, not this device. */
	hubEngineInherited: boolean;
	correctionEngine: string;
	/** Execution profile of correction jobs; set only when no device correction engine is chosen. */
	correctionExecutionProfile: string;
	/** Cleanup profile to run; empty when cleanup is off or the organization names none. */
	cleanupProfile: string;
}

export function effectiveLiveSettings(device: LiveSettings, org: OrgLiveDefaults | null | undefined): EffectiveLiveSettings {
	const orgEngine = org?.engine ?? "";
	return {
		hubEngine: device.hubEngine || orgEngine,
		hubEngineInherited: !device.hubEngine && orgEngine !== "",
		correctionEngine: device.correctionEngine,
		correctionExecutionProfile: device.correctionEngine ? "" : (org?.correction_execution_profile ?? ""),
		cleanupProfile: device.cleanup ? (org?.cleanup_profile ?? "") : "",
	};
}

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
		if (typeof saved.cleanup === "boolean") {
			target.cleanup = saved.cleanup;
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
