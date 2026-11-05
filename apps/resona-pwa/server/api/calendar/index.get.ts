import { assertCalendarEnabled } from "~~/server/utils/calendar/enabled";
import { handleCalendars, handleCallback, handleConnect, handleEvents, providerFromQuery } from "~~/server/utils/calendar/handlers";

// GET /api/calendar — the single calendar endpoint (provider via ?provider=):
//   ?code= / ?error=   → OAuth callback (provider derived from `state`)
//   ?action=connect    → redirect into the OAuth consent flow
//   ?action=calendars  → the account's selectable calendars (settings picker)
//   (otherwise)        → connection status + matching events
// 404 unless NUXT_PUBLIC_CALENDAR_ENABLED is on.
//
// One path keeps Nitro's typed-`$fetch` route union tiny (see handlers.ts) and
// lets new providers register without adding routes.
export default defineEventHandler((event) => {
	assertCalendarEnabled(useRuntimeConfig(event).public.calendarEnabled);
	const query = getQuery(event);
	if (query.code || query.error) {
		return handleCallback(event);
	}
	if (query.action === "connect") {
		return handleConnect(event, providerFromQuery(event));
	}
	if (query.action === "calendars") {
		return handleCalendars(event, providerFromQuery(event));
	}
	return handleEvents(event, providerFromQuery(event));
});
