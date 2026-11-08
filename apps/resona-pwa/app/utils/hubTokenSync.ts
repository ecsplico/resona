/**
 * Keeps the per-device hub token in step with the signed-in user's Directus
 * account (`directus_users.hub_token`, readable/writable only by that user).
 * Pure logic over a small remote interface so it is testable without Directus.
 */

export interface HubTokenRemote {
	/** The token stored on the user's account (null/empty when none). */
	read: () => Promise<string | null | undefined>;
	/** Stores the token on the account; null clears it. */
	write: (token: string | null) => Promise<void>;
}

export interface HubTokenHolder {
	token: string;
}

/** `filled`: local was empty and took the account's token; `uploaded`: the account had none and took the local one. */
export type HubTokenPullResult = "filled" | "uploaded" | "unchanged";

/**
 * Login / session restore: an empty local token is filled from the account, so
 * a second device does not ask again. A device that already has a token keeps
 * it; it is uploaded only when the account has none yet (devices set up before
 * the sync existed).
 */
export async function pullHubToken(remote: HubTokenRemote, holder: HubTokenHolder): Promise<HubTokenPullResult> {
	const local = holder.token.trim();
	const stored = (await remote.read())?.trim() ?? "";
	if (!local && stored) {
		holder.token = stored;
		return "filled";
	}
	if (local && !stored) {
		await remote.write(local);
		return "uploaded";
	}
	return "unchanged";
}

/** Token entered in Einstellungen → Verbindung: stored on the account (empty clears it). */
export async function pushHubToken(remote: HubTokenRemote, token: string): Promise<void> {
	await remote.write(token.trim() || null);
}
