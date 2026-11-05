import type { DataStore } from "~/data/types";
import { createDirectusStore } from "~/data/directusStore";
import { createLocalStore } from "~/data/localStore";

let store: DataStore | null = null;

export function useDataStore(): DataStore {
	if (!store) {
		const mode = useRuntimeConfig().public.dataMode;
		if (mode === "directus") {
			store = createDirectusStore();
		}
		else if (mode === "standalone") {
			store = createLocalStore();
		}
		else {
			throw new Error("NUXT_PUBLIC_DATA_MODE must be \"directus\" or \"standalone\"");
		}
	}
	return store;
}
