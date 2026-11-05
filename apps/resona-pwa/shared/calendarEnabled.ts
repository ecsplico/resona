// NUXT_PUBLIC_CALENDAR_ENABLED gate. Nuxt parses runtime env with `destr`
// (→ boolean), but a build-time value can stay a string, so accept both.
export function isCalendarEnabled(value: unknown): boolean {
	return value === true || value === "true";
}
