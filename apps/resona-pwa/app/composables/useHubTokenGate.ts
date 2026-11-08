import { computed } from "vue";
import type { ComputedRef } from "vue";

/** Shown wherever a recording/upload/share is refused for want of a hub token. */
export const HUB_TOKEN_REQUIRED_MESSAGE
	= "Auf diesem Gerät ist kein Hub-Token hinterlegt. Trage ihn unter Einstellungen → Verbindung ein, dann kannst du aufnehmen, hochladen und teilen.";

/**
 * Entry-point gate: without a hub token on this device nothing may be recorded,
 * uploaded or shared (the server-side worker would transcribe it anyway, which
 * hides the missing token until it bites elsewhere).
 */
export function useHubTokenGate(): {
	/** True while the device has no hub token. */
	blocked: ComputedRef<boolean>;
	message: string;
	/** Returns true when allowed; otherwise false (callers show `message`). */
	allowed: () => boolean;
} {
	const settings = useHubSettings();
	const blocked = computed(() => settings.token.trim().length === 0);
	return {
		blocked,
		message: HUB_TOKEN_REQUIRED_MESSAGE,
		allowed: () => !blocked.value,
	};
}
