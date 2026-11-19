// The practice's one Google connection: the refresh token, sealed (iron,
// NUXT_SESSION_PASSWORD) in Nitro storage `verlauf`, an fs-lite mount on
// NUXT_VERLAUF_DATA_DIR (a volume in production). The short-lived access token
// stays in memory only.
import { resolve } from "node:path";
import fsLiteDriver from "unstorage/drivers/fs-lite";
import { defaults as ironDefaults, seal, unseal } from "iron-webcrypto";
import type { VerlaufConfig } from "./config";

export interface GoogleConnection {
	refreshToken: string;
	/** Directus user id of whoever connected the calendar. */
	connectedBy: string;
	connectedAt: string;
}

const KEY = "google-connection";
let mountedBase: string | null = null;
// iron-webcrypto's `_Crypto` predates TS's ArrayBuffer-generic Uint8Array; the runtime object is the same.
const webCrypto = globalThis.crypto as unknown as Parameters<typeof seal>[0];

/** The `verlauf` storage, mounted on first use (the data dir is a runtime setting). */
function verlaufStorage(config: VerlaufConfig) {
	const base = resolve(config.dataDir);
	if (mountedBase !== base) {
		useStorage().mount("verlauf", fsLiteDriver({ base }));
		mountedBase = base;
	}
	return useStorage("verlauf");
}

/** Seal `data` with the session password; `ttlMs` 0 = no expiry. */
export async function sealValue(config: VerlaufConfig, data: unknown, ttlMs = 0): Promise<string> {
	return seal(webCrypto, data, config.sessionPassword, { ...ironDefaults, ttl: ttlMs });
}

/** Unsealed value, or null when it is forged, expired or sealed with another password. */
export async function unsealValue<T>(config: VerlaufConfig, sealed: string): Promise<T | null> {
	try {
		return await unseal(webCrypto, sealed, config.sessionPassword, ironDefaults) as T;
	}
	catch {
		return null;
	}
}

export async function readConnection(config: VerlaufConfig): Promise<GoogleConnection | null> {
	const sealed = await verlaufStorage(config).getItem<string>(KEY);
	if (typeof sealed !== "string" || !sealed) return null;
	const connection = await unsealValue<GoogleConnection>(config, sealed);
	return connection?.refreshToken ? connection : null;
}

export async function writeConnection(config: VerlaufConfig, connection: GoogleConnection): Promise<void> {
	await verlaufStorage(config).setItem(KEY, await sealValue(config, connection));
}

export async function clearConnection(config: VerlaufConfig): Promise<void> {
	await verlaufStorage(config).removeItem(KEY);
}
