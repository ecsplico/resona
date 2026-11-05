import { isCalendarEnabled } from "#shared/calendarEnabled";

// The calendar routes 404 unless NUXT_PUBLIC_CALENDAR_ENABLED is on.
export function assertCalendarEnabled(enabled: unknown): void {
	if (!isCalendarEnabled(enabled)) {
		throw createError({ statusCode: 404 });
	}
}
