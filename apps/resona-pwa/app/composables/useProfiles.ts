import { ref } from "vue";
import type { Ref } from "vue";

export function useProfiles() {
	const profiles: Ref<string[]> = ref([]);

	async function list(): Promise<string[]> {
		try {
			const names = await useHub().listProfiles();
			profiles.value = names;
			return names;
		}
		catch {
			const fallback = [useRuntimeConfig().public.defaultProfile];
			profiles.value = fallback;
			return fallback;
		}
	}

	return {
		profiles,
		list,
	};
}
