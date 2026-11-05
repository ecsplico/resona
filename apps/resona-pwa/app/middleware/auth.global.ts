export default defineNuxtRouteMiddleware(async (to) => {
	if (useRuntimeConfig().public.dataMode === "standalone") {
		return to.path === "/login" ? navigateTo("/") : undefined;
	}
	const { isAuthenticated, restoreSession } = useDirectus();
	if (to.path === "/login") return;
	if (import.meta.client && !isAuthenticated.value) {
		await restoreSession();
	}
	if (!isAuthenticated.value) return navigateTo("/login");
});
