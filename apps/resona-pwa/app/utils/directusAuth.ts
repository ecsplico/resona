import { Capacitor } from "@capacitor/core";
import { Preferences } from "@capacitor/preferences";
import type { AuthenticationData, AuthenticationStorage } from "@directus/sdk";

/** Key of the stored Directus session, in localStorage (web) and Capacitor Preferences (app). */
export const AUTH_STORAGE_KEY = "resona.auth";

/**
 * How the Directus SDK keeps the refresh token.
 *
 * - `cookie` (web): an httpOnly cookie of the Directus host, first-party through the
 *   PWA's `/directus` proxy; also what the Keycloak SSO callback sets.
 * - `json` (Capacitor app): the app origin (`https://localhost`, `capacitor://localhost`)
 *   is cross-site to Directus, so the `SameSite=Lax` refresh cookie is never sent and
 *   the session would end at the first access-token expiry. The refresh token travels
 *   in the request body instead and lives in app storage.
 */
export type DirectusAuthMode = "cookie" | "json";

export function directusAuthMode(): DirectusAuthMode {
	return Capacitor.isNativePlatform() ? "json" : "cookie";
}

/**
 * Web: the session in localStorage, so it survives a reload (the SDK default is
 * in memory only). In cookie mode it holds the access token and its expiry; the
 * refresh token stays in the cookie.
 */
export function localStorageAuthStorage(key = AUTH_STORAGE_KEY): AuthenticationStorage {
	const ls = (): Storage | null =>
		typeof localStorage === "undefined" ? null : localStorage;
	return {
		get() {
			const raw = ls()?.getItem(key);
			return raw ? (JSON.parse(raw) as AuthenticationData) : null;
		},
		set(value: AuthenticationData | null) {
			const store = ls();
			if (!store) return;
			if (value) store.setItem(key, JSON.stringify(value));
			else store.removeItem(key);
		},
	};
}

/**
 * App: access and refresh token in Capacitor Preferences (Android SharedPreferences,
 * iOS UserDefaults; app-private, kept across restarts and not evicted like web view
 * storage under memory pressure). A memory copy answers the SDK's per-request reads
 * without a native bridge call; `set` updates it before writing, so a read right after
 * a logout never sees the old tokens.
 */
export function preferencesAuthStorage(key = AUTH_STORAGE_KEY): AuthenticationStorage {
	let cache: AuthenticationData | null | undefined;
	return {
		async get() {
			if (cache === undefined) {
				const { value } = await Preferences.get({ key });
				// set() may have run while the native read was in flight: it wins.
				if (cache === undefined) cache = value ? (JSON.parse(value) as AuthenticationData) : null;
			}
			return cache;
		},
		async set(value: AuthenticationData | null) {
			// The SDK writes a token-less session on logout and before storing a refreshed one.
			if (!value || (!value.access_token && !value.refresh_token)) {
				cache = null;
				await Preferences.remove({ key });
				return;
			}
			cache = value;
			await Preferences.set({ key, value: JSON.stringify(value) });
		},
	};
}
