import { ref } from "vue";
import type { Ref } from "vue";
import { humanizeError } from "#shared/errors";

export function useProfiles() {
	const profiles: Ref<string[]> = ref([]);
	// Why the hub list could not be loaded; `profiles` then holds only the configured
	// default (enough for an upload, which the hub validates later). Callers that run a
	// profile right away (Nachbearbeiten) show this instead of offering the fallback.
	const error: Ref<string | null> = ref(null);

	async function list(): Promise<string[]> {
		try {
			const names = await useHub().listProfiles();
			profiles.value = names;
			error.value = null;
			return names;
		}
		catch (err) {
			error.value = humanizeError(err, "Profile konnten nicht geladen werden.");
			const fallback = [useRuntimeConfig().public.defaultProfile];
			profiles.value = fallback;
			return fallback;
		}
	}

	return {
		profiles,
		error,
		list,
	};
}
