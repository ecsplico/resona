import { readMe, updateMe } from "@directus/sdk";
import { pullHubToken, pushHubToken } from "~/utils/hubTokenSync";
import type { HubTokenPullResult, HubTokenRemote } from "~/utils/hubTokenSync";

/**
 * Per-user hub token on the Directus account (directus mode only; standalone
 * has no account, the token stays purely per device). `pull` runs after login
 * and session restore, `push` when the token is entered in the settings.
 */
export function useHubTokenSync(): {
	enabled: () => boolean;
	pull: () => Promise<HubTokenPullResult>;
	push: (token: string) => Promise<boolean>;
} {
	const settings = useHubSettings();
	// Read now: callers run later, outside the Nuxt context.
	const isDirectus = useRuntimeConfig().public.dataMode === "directus";
	const enabled = (): boolean => isDirectus;

	const remote: HubTokenRemote = {
		async read() {
			const me = await useDirectus().client.request(readMe({ fields: ["hub_token"] }));
			return me.hub_token;
		},
		async write(token) {
			await useDirectus().client.request(updateMe({ hub_token: token }));
		},
	};

	return {
		enabled,
		pull: async () => (enabled() ? await pullHubToken(remote, settings) : "unchanged"),
		push: async (token) => {
			if (!enabled()) {
				return false;
			}
			await pushHubToken(remote, token);
			return true;
		},
	};
}
