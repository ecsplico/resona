import { createDirectus, authentication, rest } from "@directus/sdk";
import type { AuthenticationClient, AuthenticationStorage, DirectusClient, RestClient } from "@directus/sdk";
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
let refreshing: Promise<unknown> | null = null;

function clearLocalSession(): Promise<unknown> {
	client?.stopRefreshing();
	accessToken.value = "";
	isAuthenticated.value = false;
	return Promise.resolve(authStorage?.set(null));
}

function errorStatus(err: unknown): number | undefined {
	return (err as { response?: { status?: number } } | null)?.response?.status;
}

/**
 * Directus refused the refresh token itself (expired, revoked, already rotated):
 * 401 or 400 from /auth/refresh. Anything else (offline TypeError, timeout, 5xx)
 * says nothing about the session, and in the app the stored refresh token is the
 * only way back in, so it must survive those.
 */
function refreshRejected(err: unknown): boolean {
	const status = errorStatus(err);
	return status === 401 || status === 400;
}

// One explicit refresh at a time: the refresh token rotates, so a second
// concurrent refresh would send the spent token and be rejected. The SDK's
// refresh() does not join one its getToken() or timer already started; the 401
// path below waits for those through getToken() before it gets here.
function refreshSession(c: ResonaDirectusClient): Promise<unknown> {
	refreshing ??= c.refresh().finally(() => {
		refreshing = null;
	});
	return refreshing;
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
		authStorage = authMode === "json" ? preferencesAuthStorage() : localStorageAuthStorage();
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
		// requests 401 ("Token expired.") while the page still looks logged in. On a
		// 401: retry with a newer token if one arrived meanwhile, else refresh once and
		// retry; when Directus rejects the refresh token the session is over (back to
		// /login); a transient refresh failure keeps it.
		const request = base.request.bind(base);
		base.request = (async (options: Parameters<typeof request>[0]) => {
			// The token this request goes out with: rest() asks getToken() again right
			// after and gets the same one (it refreshes only near expiry, and this call
			// already did).
			const used = await base.getToken();
			try {
				return await request(options);
			}
			catch (err) {
				if (errorStatus(err) !== 401 || !isAuthenticated.value) throw err;
				// Waits for a refresh the SDK has in flight (getToken() of another request,
				// the web timer): typical on resume, when a request frozen in the
				// background is sent late with the old token. Refreshing again would send
				// the spent refresh token and be rejected.
				const current = await base.getToken();
				if (!current || current === used) {
					try {
						await refreshSession(base);
					}
					catch (refreshErr) {
						if (refreshRejected(refreshErr) && isAuthenticated.value) {
							void clearLocalSession();
							void nuxtApp?.runWithContext(() => navigateTo("/login", { replace: true }));
						}
						throw err;
					}
				}
				return await request(options);
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
		// so refresh an expired session here and look at why it failed.
		const stored = await authStorage?.get();
		if (stored?.expires_at != null && stored.expires_at <= Date.now() + REFRESH_MARGIN_MS) {
			try {
				await refreshSession(directus);
			}
			catch (err) {
				if (refreshRejected(err)) {
					await clearLocalSession();
					return false;
				}
				// Offline or Directus down (e.g. a cold start without network): the
				// stored tokens stay and the session counts as signed in. The next
				// request's getToken() refreshes once Directus is reachable; a rejected
				// refresh then ends the session through the 401 path above.
				accessToken.value = stored.access_token ?? "";
				isAuthenticated.value = !!(stored.access_token || stored.refresh_token);
				return isAuthenticated.value;
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

	// Async: with JSON auth nothing refreshes in the background (autoRefresh off),
	// so the token is brought up to date (getToken refreshes it within 30 s of
	// expiry) before it goes into a URL the browser fetches without the SDK.
	async function assetUrl(fileId: string): Promise<string> {
		if (isAuthenticated.value) {
			const token = await directus.getToken();
			if (token) accessToken.value = token;
		}
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
