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

function getClient(): ResonaDirectusClient {
	if (!client) {
		directusUrl = useRuntimeConfig().public.directusUrl;
		client = createDirectus<Schema>(directusUrl)
			.with(authentication("json", { storage: persistentAuthStorage(), autoRefresh: true }))
			.with(rest());
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
		logout,
		restoreSession,
		assetUrl,
	};
}
