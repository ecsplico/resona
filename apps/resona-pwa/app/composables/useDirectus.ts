import { createDirectus, authentication, rest } from "@directus/sdk";
import type { AuthenticationClient, AuthenticationData, AuthenticationStorage, DirectusClient, RestClient } from "@directus/sdk";
import { ref } from "vue";
import type { Ref } from "vue";
import type { Schema } from "~/types/directus";

type ResonaDirectusClient = DirectusClient<Schema> & AuthenticationClient<Schema> & RestClient<Schema>;

const STORAGE_KEY = "resona.auth";

// Persist the session (access + refresh tokens) in localStorage so it survives a
// page refresh — the SDK's default storage is in-memory only, which logged the
// user out on every reload. This also makes "Remember me" implicit: the refresh
// token is kept and auto-refreshed, so the session sticks until explicit logout.
function persistentAuthStorage(): AuthenticationStorage {
	const ls = (): Storage | null =>
		typeof localStorage === "undefined" ? null : localStorage;
	return {
		get() {
			const raw = ls()?.getItem(STORAGE_KEY);
			return raw ? (JSON.parse(raw) as AuthenticationData) : null;
		},
		set(value: AuthenticationData | null) {
			const store = ls();
			if (!store) return;
			if (value) store.setItem(STORAGE_KEY, JSON.stringify(value));
			else store.removeItem(STORAGE_KEY);
		},
	};
}

let client: ResonaDirectusClient | null = null;
let directusUrl = "";
const isAuthenticated: Ref<boolean> = ref(false);
const accessToken: Ref<string> = ref("");
const authStorage = persistentAuthStorage();
// Refresh this long before expires_at, matching the SDK's msRefreshBeforeExpires.
const REFRESH_MARGIN_MS = 30_000;

function clearLocalSession(): void {
	authStorage.set(null);
	client?.stopRefreshing();
	accessToken.value = "";
	isAuthenticated.value = false;
}

function getClient(): ResonaDirectusClient {
	if (!client) {
		directusUrl = useRuntimeConfig().public.directusUrl;
		// Captured now (first use runs in the auth middleware), so a 401 from a
		// later async request can still navigate.
		const nuxtApp = tryUseNuxtApp();
		const base = createDirectus<Schema>(directusUrl)
			.with(authentication("json", { storage: authStorage, autoRefresh: true }))
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
					clearLocalSession();
					void nuxtApp?.runWithContext(() => navigateTo("/login"));
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

	async function login(email: string, password: string): Promise<void> {
		await directus.login({ email, password });
		accessToken.value = (await directus.getToken()) ?? "";
		isAuthenticated.value = true;
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
			clearLocalSession();
		}
		return isAuthenticated.value;
	}

	async function logout(): Promise<void> {
		try {
			await directus.logout();
		}
		catch {
			// ignore — clear local auth state regardless
		}
		accessToken.value = "";
		isAuthenticated.value = false;
	}

	async function restoreSession(): Promise<boolean> {
		// getToken() would return the stale access token when its refresh fails,
		// so refresh an expired session here and treat failure as logged out.
		const stored = await authStorage.get();
		if (stored?.expires_at != null && stored.expires_at <= Date.now() + REFRESH_MARGIN_MS) {
			try {
				await directus.refresh();
			}
			catch {
				clearLocalSession();
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
		login,
		loginWithSso,
		completeSso,
		logout,
		restoreSession,
		assetUrl,
	};
}
