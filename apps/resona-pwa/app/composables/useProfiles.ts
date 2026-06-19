import { ref } from "vue";
import type { Ref } from "vue";

type RawProfile = string | { name?: string };

export function useProfiles() {
	const profiles: Ref<string[]> = ref([]);

	async function list(): Promise<string[]> {
		const cfg = useRuntimeConfig().public;
		try {
			const raw = await $fetch(`${cfg.resonaApiUrl}/profiles/`) as RawProfile[];
			const names = (Array.isArray(raw) ? raw : [])
				.map(p => (typeof p === "string" ? p : p?.name))
				.filter((n): n is string => typeof n === "string" && n.length > 0);
			profiles.value = names;
			return names;
		}
		catch {
			const fallback = [cfg.defaultProfile];
			profiles.value = fallback;
			return fallback;
		}
	}

	return {
		profiles,
		list,
	};
}
