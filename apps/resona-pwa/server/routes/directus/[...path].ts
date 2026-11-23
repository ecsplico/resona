import { proxyDirectus } from "../../utils/directusProxy";

export default defineEventHandler((event) => {
	const config = useRuntimeConfig(event);
	return proxyDirectus(event, `/${getRouterParam(event, "path") ?? ""}`, {
		base: config.directusInternalUrl,
		cookieDomain: config.directusCookieDomain,
	});
});
