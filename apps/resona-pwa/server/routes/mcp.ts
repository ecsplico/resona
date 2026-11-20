// Same-origin proxy to the dictation MCP service (mcp/, resona-dictations-mcp), so it
// needs no public host: MCP clients connect to <app origin>/mcp with their Directus
// token as `Authorization: Bearer`. The service does all authentication itself; this
// route only forwards. Browser cookies (the Directus refresh token) are not passed on.
export default defineEventHandler((event) => {
	const base = useRuntimeConfig(event).dictationsMcpInternalUrl;
	if (!base) {
		throw createError({ statusCode: 404, statusMessage: "Not Found" });
	}
	return proxyRequest(event, `${base.replace(/\/$/, "")}/mcp`, {
		headers: { cookie: "" },
		fetchOptions: { redirect: "manual" },
	});
});
