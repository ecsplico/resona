import { reactive, watch } from "vue";

/**
 * Persisted calendar preferences: which calendar to read and the default filter
 * criteria the calendar page seeds from. Stored client-side in localStorage —
 * the PWA has no per-user DB, and these are non-secret view preferences (the
 * OAuth tokens stay in the server-sealed cookie). Exposed as a module singleton
 * so the settings drawer and the calendar page share one reactive object.
 */
export interface CalendarSettings {
	/** Selected calendar id; empty → the account's primary calendar. */
	calendarId: string;
	/** Default free-text event filter. */
	query: string;
	/** Days before today the default window starts. */
	daysBack: number;
	/** Days after today the default window ends. */
	daysAhead: number;
}

const STORAGE_KEY = "resona.calendar.settings";

const DEFAULTS: CalendarSettings = {
	calendarId: "",
	query: "",
	daysBack: 0,
	daysAhead: 30,
};

let singleton: CalendarSettings | null = null;

function load(target: CalendarSettings): void {
	if (typeof window === "undefined") {
		return;
	}
	try {
		const raw = window.localStorage.getItem(STORAGE_KEY);
		if (!raw) {
			return;
		}
		const saved = JSON.parse(raw) as Partial<CalendarSettings>;
		if (typeof saved.calendarId === "string") {
			target.calendarId = saved.calendarId;
		}
		if (typeof saved.query === "string") {
			target.query = saved.query;
		}
		if (typeof saved.daysBack === "number") {
			target.daysBack = saved.daysBack;
		}
		if (typeof saved.daysAhead === "number") {
			target.daysAhead = saved.daysAhead;
		}
	}
	catch {
		// Corrupt/blocked storage — fall back to defaults.
	}
}

export function useCalendarSettings(): CalendarSettings {
	if (!singleton) {
		const state = reactive<CalendarSettings>({ ...DEFAULTS });
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
