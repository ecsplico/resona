// Same-origin proxy for the dictation MCP service's signed links (mcp/links.py):
// `create_audio_upload` upload URLs (PUT/POST, body streamed, not buffered) and
// `get_audio_link` audio URLs (GET, Range passed on). The link token is the credential;
// the service checks it. Browser cookies are not passed on.
export default defineEventHandler((event) => {
	const base = useRuntimeConfig(event).dictationsMcpInternalUrl;
	const path = getRouterParam(event, "path") ?? "";
	if (!base || !/^(upload|audio)\/[\w.-]+$/.test(path)) {
		throw createError({ statusCode: 404, statusMessage: "Not Found" });
	}
	return proxyRequest(event, `${base.replace(/\/$/, "")}/mcp/links/${path}`, {
		headers: { cookie: "" },
		streamRequest: true,
		fetchOptions: { redirect: "manual" },
	});
});
