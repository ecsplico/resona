import { ref } from "vue";
import type { Ref } from "vue";
import { readUsers, updateMe } from "@directus/sdk";
import { Capacitor } from "@capacitor/core";
import { generateMcpToken } from "~/utils/mcpSetup";

/**
 * - `available`: directus mode in the browser
 * - `standalone`: no account, the dictations live in this browser only
 * - `app`: the Capacitor app, whose origin (`https://localhost`) is not the
 *   server's, so it could not name the MCP address
 */
export type McpTokenAvailability = "available" | "standalone" | "app";

export interface McpToken {
	availability: McpTokenAvailability;
	/** Whether the account has a token; null until checked (or the check failed). */
	exists: Ref<boolean | null>;
	/** The token just created, shown once; never filled from the server. */
	shown: Ref<string | null>;
	busy: Ref<boolean>;
	error: Ref<string | null>;
	check: () => Promise<boolean>;
	/** Creates a token, replacing the current one if there is one. */
	create: () => Promise<boolean>;
	revoke: () => Promise<boolean>;
	hide: () => void;
}

/**
 * The user's personal MCP token = their static Directus token
 * (`directus_users.token`), which the dictation MCP (`<origin>/mcp`) accepts as
 * `Authorization: Bearer`. It has the same rights as the user's login.
 *
 * The app never asks Directus for the `token` field: it is written with
 * `PATCH /users/me` (response limited to `id`), and whether one exists is asked
 * with a filter (`token _nnull`) that returns only the own row's `id`. The value
 * lives only in `shown`, per instance, so it is gone once the settings close.
 */
export function useMcpToken(): McpToken {
	const availability: McpTokenAvailability = useRuntimeConfig().public.dataMode !== "directus"
		? "standalone"
		: Capacitor.isNativePlatform() ? "app" : "available";
	const exists = ref<boolean | null>(null);
	const shown = ref<string | null>(null);
	const busy = ref(false);
	const error = ref<string | null>(null);

	async function run(action: () => Promise<void>, failure: string): Promise<boolean> {
		if (availability !== "available" || busy.value) {
			return false;
		}
		busy.value = true;
		error.value = null;
		try {
			await action();
			return true;
		}
		catch {
			error.value = failure;
			return false;
		}
		finally {
			busy.value = false;
		}
	}

	return {
		availability,
		exists,
		shown,
		busy,
		error,
		check: () => run(async () => {
			const rows = await useDirectus().client.request(readUsers({
				filter: { id: { _eq: "$CURRENT_USER" }, token: { _nnull: true } },
				fields: ["id"],
				limit: 1,
			}));
			exists.value = rows.length > 0;
		}, "Ob ein Token besteht, konnte nicht geprüft werden."),
		create: () => run(async () => {
			const token = generateMcpToken();
			await useDirectus().client.request(updateMe({ token }, { fields: ["id"] }));
			exists.value = true;
			shown.value = token;
		}, "Der Token konnte nicht gespeichert werden. Bitte erneut versuchen."),
		revoke: () => run(async () => {
			await useDirectus().client.request(updateMe({ token: null }, { fields: ["id"] }));
			exists.value = false;
			shown.value = null;
		}, "Der Token konnte nicht widerrufen werden. Bitte erneut versuchen."),
		hide: () => {
			shown.value = null;
		},
	};
}
