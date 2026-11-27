import { ref, type Ref } from "vue";
import type { OrgLiveDefaults } from "~/composables/useHub";

/**
 * The organization's live defaults (`OrganizationSettings.live` from `GET /v1/settings`),
 * which empty device settings inherit. A module singleton shared by the settings drawer
 * and the live page. Null until loaded, and when the hub is unreachable or has no token:
 * the device values then stand alone and the hub applies its own defaults.
 */
const defaults: Ref<OrgLiveDefaults | null> = ref(null);
let inflight: Promise<OrgLiveDefaults | null> | null = null;

export function useOrgLiveDefaults() {
	const hub = useHub();

	/** Fetch the current defaults (one request at a time); keeps the last value on failure. */
	function load(): Promise<OrgLiveDefaults | null> {
		if (!inflight) {
			inflight = Promise.resolve()
				.then(async () => {
					if (!hub.hasToken()) return defaults.value;
					const live = (await hub.getSettings())?.settings?.live;
					defaults.value = live && typeof live === "object" ? { ...live } : {};
					return defaults.value;
				})
				.catch(() => defaults.value)
				.finally(() => {
					inflight = null;
				});
		}
		return inflight;
	}

	return { defaults, load };
}
