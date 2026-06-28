import { reactive, ref } from "vue";
import type { Ref } from "vue";
import type { CalendarEvent, CalendarFilter, CalendarListView, CalendarOption, CalendarStatus, CalendarView } from "#shared/calendar";
import { useCalendarSettings } from "./useCalendarSettings";

/** ISO at local midnight, `dayOffset` days from today (for the date filter). */
function isoAtMidnight(dayOffset: number): string {
	const d = new Date();
	d.setHours(0, 0, 0, 0);
	d.setDate(d.getDate() + dayOffset);
	return d.toISOString();
}

/** Local date (`YYYY-MM-DD`) for binding `<input type="date">`. */
function toDateInput(iso: string): string {
	const d = new Date(iso);
	if (Number.isNaN(d.getTime())) {
		return "";
	}
	const pad = (n: number) => String(n).padStart(2, "0");
	return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * Calendar integration client. Provider-agnostic: every call hits the uniform
 * `/api/calendar/:provider/**` Nitro surface, so wiring a second provider is
 * purely a server-side + metadata change.
 */
export function useCalendar(provider = "google") {
	const settings = useCalendarSettings();
	const status: Ref<CalendarStatus | null> = ref(null);
	const events: Ref<CalendarEvent[]> = ref([]);
	const loading = ref(false);
	const error: Ref<string | null> = ref(null);

	// Default window + query seeded from the saved settings (today−daysBack →
	// today+daysAhead). The date inputs in the UI bind to this for ad-hoc tweaks.
	const filter = reactive<CalendarFilter & { from: string; to: string }>({
		from: toDateInput(isoAtMidnight(-settings.daysBack)),
		to: toDateInput(isoAtMidnight(settings.daysAhead)),
		query: settings.query,
	});

	/** Full-page link that kicks off the OAuth flow (used as an `<a href>`). */
	const connectUrl = `/api/calendar?provider=${encodeURIComponent(provider)}&action=connect`;

	/**
	 * Fetch connection status + events for the current filter in one call.
	 * Cheap when disconnected (the server skips the provider round-trip).
	 */
	async function refresh(): Promise<void> {
		loading.value = true;
		error.value = null;
		try {
			const fromIso = filter.from ? new Date(`${filter.from}T00:00:00`).toISOString() : undefined;
			const toIso = filter.to ? new Date(`${filter.to}T23:59:59`).toISOString() : undefined;
			// Literal path (not a template) → cheap exact route match, avoiding the
			// typed-`$fetch` deep-recursion issue noted in the server handlers.
			const view = await $fetch<CalendarView>("/api/calendar", {
				query: {
					provider,
					timeMin: fromIso,
					timeMax: toIso,
					q: filter.query || undefined,
					calendarId: settings.calendarId || undefined,
				},
			});
			status.value = {
				provider: view.provider,
				configured: view.configured,
				connected: view.connected,
				email: view.email,
			};
			events.value = view.events;
			if (view.error) {
				error.value = "Termine konnten nicht geladen werden.";
			}
		}
		catch {
			status.value = status.value ?? { provider, configured: false, connected: false };
			events.value = [];
			error.value = "Kalender ist nicht erreichbar.";
		}
		finally {
			loading.value = false;
		}
	}

	/**
	 * Fetch the account's selectable calendars + connection state (settings
	 * picker). Also refreshes `status` so callers get connect/configured flags.
	 */
	async function listCalendars(): Promise<CalendarOption[]> {
		try {
			const res = await $fetch<CalendarListView>("/api/calendar", {
				query: { provider, action: "calendars" },
			});
			status.value = {
				provider,
				configured: res.configured,
				connected: res.connected,
				email: status.value?.email,
			};
			return res.calendars;
		}
		catch {
			return [];
		}
	}

	async function disconnect(): Promise<void> {
		try {
			await $fetch("/api/calendar", { method: "DELETE", query: { provider } });
		}
		catch {
			// ignore — refresh below reflects the real state
		}
		events.value = [];
		await refresh();
	}

	return {
		provider,
		status,
		events,
		loading,
		error,
		filter,
		settings,
		connectUrl,
		refresh,
		listCalendars,
		disconnect,
	};
}
