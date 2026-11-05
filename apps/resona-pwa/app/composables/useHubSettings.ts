import { reactive, watch } from "vue";

/**
 * Per-device hub connection settings. The hub token is entered once per device
 * in Einstellungen → Verbindung and kept in localStorage — nothing about it is
 * baked into the bundle. Exposed as a module singleton so the settings drawer
 * and every hub caller share one reactive object.
 */
export interface HubSettings {
	/** Org-bound hub token; empty → not configured on this device. */
	token: string;
}

const STORAGE_KEY = "resona.hub.settings";

const DEFAULTS: HubSettings = {
	token: "",
};

let singleton: HubSettings | null = null;

function load(target: HubSettings): void {
	if (typeof window === "undefined") {
		return;
	}
	try {
		const raw = window.localStorage.getItem(STORAGE_KEY);
		if (!raw) {
			return;
		}
		const saved = JSON.parse(raw) as Partial<HubSettings>;
		if (typeof saved.token === "string") {
			target.token = saved.token;
		}
	}
	catch {
		// Corrupt/blocked storage — fall back to defaults.
	}
}

export function useHubSettings(): HubSettings {
	if (!singleton) {
		const state = reactive<HubSettings>({ ...DEFAULTS });
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
