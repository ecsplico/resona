import { watch } from "vue";

/**
 * The list views keep their rows and filters in shared state (see `LIST_STATE_KEYS`);
 * drop them when the session ends (logout, expired token) so the next login starts clean.
 */
export default defineNuxtPlugin(() => {
	if (useRuntimeConfig().public.dataMode === "standalone") return;

	const { isAuthenticated } = useDirectus();
	watch(isAuthenticated, (authenticated) => {
		if (!authenticated) clearNuxtState([...LIST_STATE_KEYS]);
	});
});
