// Provider registry — the single place that knows which calendar backends
// exist. Add a provider by importing it and adding one entry to `PROVIDERS`.
import { googleProvider } from "./google";
import type { CalendarProvider } from "./types";

const PROVIDERS: Record<string, CalendarProvider> = {
	google: googleProvider,
};

/** Names of all registered providers. */
export function calendarProviderNames(): string[] {
	return Object.keys(PROVIDERS);
}

/**
 * Resolve a provider by name. Throws a 404 H3 error for unknown names so the
 * routes surface a clean response.
 */
export function getCalendarProvider(name: string | undefined): CalendarProvider {
	const provider = name ? PROVIDERS[name] : undefined;
	if (!provider) {
		throw createError({
			statusCode: 404,
			statusMessage: `Unknown calendar provider '${name}'. Known: ${calendarProviderNames().join(", ")}`,
		});
	}
	return provider;
}
