import { assertCalendarEnabled } from "../../utils/calendar/enabled";
import { handleDisconnect, providerFromQuery } from "../../utils/calendar/handlers";

// DELETE /api/calendar?provider=… → disconnect + revoke (404 unless the flag is on).
export default defineEventHandler((event) => {
	assertCalendarEnabled(useRuntimeConfig(event).public.calendarEnabled);
	return handleDisconnect(event, providerFromQuery(event));
});
