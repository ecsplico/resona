import { createDirectus, authentication, rest } from "@directus/sdk";
import type { AuthenticationClient, AuthenticationData, AuthenticationStorage, DirectusClient, RestClient } from "@directus/sdk";
import { directusAuthMode, localStorageAuthStorage, preferencesAuthStorage } from "~/utils/directusAuth";
import type { DirectusAuthMode } from "~/utils/directusAuth";
import { ref } from "vue";
import type { Ref } from "vue";
import type { Schema } from "~/types/directus";

type ResonaDirectusClient = DirectusClient<Schema> & AuthenticationClient<Schema> & RestClient<Schema>;

let client: ResonaDirectusClient | null = null;
let directusUrl = "";
let authMode: DirectusAuthMode = "cookie";
let authStorage: AuthenticationStorage | null = null;
const isAuthenticated: Ref<boolean> = ref(false);
const accessToken: Ref<string> = ref("");
// Refresh this long before expires_at, matching the SDK's msRefreshBeforeExpires.
const REFRESH_MARGIN_MS = 30_000;

function clearLocalSession(): Promise<unknown> {
	client?.stopRefreshing();
	accessToken.value = "";
	isAuthenticated.value = false;
	return Promise.resolve(authStorage?.set(null));
}

// Keeps `accessToken` (used in asset URLs) on the latest token: a refresh only
// writes the storage, so asset URLs built later would otherwise carry the expired
// login-time token. The token-less write before a refreshed session is skipped.
function trackAccessToken(storage: AuthenticationStorage): AuthenticationStorage {
	return {
		get: () => storage.get(),
		set(value: AuthenticationData | null) {
			if (value?.access_token && isAuthenticated.value) accessToken.value = value.access_token;
			return storage.set(value);
		},
	};
}

function getClient(): ResonaDirectusClient {
	if (!client) {
		directusUrl = useRuntimeConfig().public.directusUrl;
		authMode = directusAuthMode();
		// Session persistence (the SDK default is in memory only, which logged the user
		// out on every reload). Web: cookie mode, the refresh token in the httpOnly
		// cookie (set by password login and the Keycloak SSO callback alike), only the
		// access token in localStorage. App: JSON mode, both tokens in Preferences, see
		// utils/directusAuth.ts.
		authStorage = trackAccessToken(authMode === "json" ? preferencesAuthStorage() : localStorageAuthStorage());
		// Captured now (first use runs in the auth middleware), so a 401 from a
		// later async request can still navigate.
		const nuxtApp = tryUseNuxtApp();
		const auth = authMode === "json"
			// No refresh timer in the app: Android/iOS freeze it in the background and
			// it would fire on resume alongside the first request's refresh, sending the
			// same (rotating) refresh token twice. getToken() before every request
			// refreshes a token within 30 s of expiry instead (one refresh in flight).
			? authentication("json", { storage: authStorage, autoRefresh: false })
			: authentication("cookie", { storage: authStorage, autoRefresh: true, credentials: "include" });
		const base = createDirectus<Schema>(directusUrl)
			.with(auth)
			.with(rest());
		// The SDK swallows a failed refresh and keeps sending the dead token, so
		// every request 401s ("Token expired.") while the page still looks logged
		// in. Treat any 401 as the end of the session and go back to /login.
		const request = base.request.bind(base);
		base.request = (async (options: Parameters<typeof request>[0]) => {
			try {
				return await request(options);
			}
			catch (err) {
				const status = (err as { response?: { status?: number } } | null)?.response?.status;
				if (status === 401 && isAuthenticated.value) {
					void clearLocalSession();
					void nuxtApp?.runWithContext(() => navigateTo("/login", { replace: true }));
				}
				throw err;
			}
		}) as typeof base.request;
		client = base;
	}
	return client;
}

export function useDirectus() {
	const directus = getClient();
	const hubTokenSync = useHubTokenSync();

	// A fresh session fills an empty device token from the user's account. Best
	// effort: a failure here (old Directus without the field/permission) never
	// blocks the sign-in.
	async function syncHubToken(): Promise<void> {
		try {
			await hubTokenSync.pull();
		}
		catch (err) {
			console.warn("[hub-token] sync failed", err);
		}
	}

	async function login(email: string, password: string): Promise<void> {
		await directus.login({ email, password });
		accessToken.value = (await directus.getToken()) ?? "";
		isAuthenticated.value = true;
		await syncHubToken();
	}

	// Single sign-on through a Directus auth provider (Keycloak). Directus runs the
	// OpenID flow and returns to `returnPath` with its refresh-token cookie set.
	function loginWithSso(provider: string, returnPath: string): void {
		const redirect = encodeURIComponent(`${window.location.origin}${returnPath}`);
		window.location.assign(`${directusUrl}/auth/login/${provider}?redirect=${redirect}`);
	}

	// Called on the page SSO returns to: trades the refresh-token cookie for a session.
	async function completeSso(): Promise<boolean> {
		try {
			await directus.refresh();
			accessToken.value = (await directus.getToken()) ?? "";
			isAuthenticated.value = !!accessToken.value;
		}
		catch {
			await clearLocalSession();
		}
		if (isAuthenticated.value) await syncHubToken();
		return isAuthenticated.value;
	}

	async function logout(): Promise<void> {
		try {
			await directus.logout();
		}
		catch {
			// ignore — clear local auth state regardless
		}
		// The SDK keeps the stored tokens when its logout request fails (offline);
		// in the app that would leave the refresh token behind.
		await clearLocalSession();
	}

	async function restoreSession(): Promise<boolean> {
		// getToken() would return the stale access token when its refresh fails,
		// so refresh an expired session here and treat failure as logged out.
		const stored = await authStorage?.get();
		if (stored?.expires_at != null && stored.expires_at <= Date.now() + REFRESH_MARGIN_MS) {
			try {
				await directus.refresh();
			}
			catch {
				await clearLocalSession();
				return false;
			}
		}
		try {
			const token = await directus.getToken();
			accessToken.value = token ?? "";
			isAuthenticated.value = !!token;
		}
		catch {
			accessToken.value = "";
			isAuthenticated.value = false;
		}
		if (isAuthenticated.value) await syncHubToken();
		return isAuthenticated.value;
	}

	function assetUrl(fileId: string): string {
		return accessToken.value
			? `${directusUrl}/assets/${fileId}?access_token=${accessToken.value}`
			: `${directusUrl}/assets/${fileId}`;
	}

	return {
		client: directus,
		isAuthenticated,
		/** Keycloak SSO works only in the web build (cookie mode); see docs/guide/pwa-app.md. */
		ssoSupported: authMode === "cookie",
		login,
		loginWithSso,
		completeSso,
		logout,
		restoreSession,
		assetUrl,
	};
}
