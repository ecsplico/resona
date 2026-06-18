export default defineNuxtRouteMiddleware(async (to) => {
	const { isAuthenticated, restoreSession } = useDirectus();
	if (to.path === "/login") return;
	if (import.meta.client && !isAuthenticated.value) {
		await restoreSession();
	}
	if (!isAuthenticated.value) return navigateTo("/login");
});
