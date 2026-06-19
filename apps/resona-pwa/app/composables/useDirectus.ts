import { createDirectus, authentication, rest } from "@directus/sdk";
import type { AuthenticationClient, DirectusClient, RestClient } from "@directus/sdk";
import { ref } from "vue";
import type { Ref } from "vue";
import type { Schema } from "~/types/directus";

type ResonaDirectusClient = DirectusClient<Schema> & AuthenticationClient<Schema> & RestClient<Schema>;

let client: ResonaDirectusClient | null = null;
let directusUrl = "";
const isAuthenticated: Ref<boolean> = ref(false);
const accessToken: Ref<string> = ref("");

function getClient(): ResonaDirectusClient {
	if (!client) {
		directusUrl = useRuntimeConfig().public.directusUrl;
		client = createDirectus<Schema>(directusUrl)
			.with(authentication("json"))
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
