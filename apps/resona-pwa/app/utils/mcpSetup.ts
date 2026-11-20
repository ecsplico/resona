/** Name the MCP clients list the dictation server under. */
export const MCP_SERVER_NAME = "resona";

export interface McpSnippet {
	key: "claude-code" | "claude-desktop" | "cursor";
	label: string;
	/** Where the snippet goes, one short German line. */
	where: string;
	text: string;
}

/** The dictation MCP endpoint of this app: `<origin>/mcp` (server/routes/mcp.ts). */
export function mcpUrl(origin: string): string {
	return `${origin.replace(/\/$/, "")}/mcp`;
}

/**
 * 32 random bytes from the browser's CSPRNG, base64url without padding (43
 * characters): the user's static Directus token.
 */
export function generateMcpToken(): string {
	const bytes = new Uint8Array(32);
	crypto.getRandomValues(bytes);
	let binary = "";
	for (const byte of bytes) {
		binary += String.fromCharCode(byte);
	}
	return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * Ready-to-paste setup for Claude Code, Claude Desktop and Cursor. Claude Desktop's
 * config file only starts local (stdio) servers, so it reaches the HTTP endpoint
 * through `mcp-remote`; the header value sits in `env` because a space inside an
 * `args` entry is mangled on Windows.
 */
export function mcpSnippets(origin: string, token: string): McpSnippet[] {
	const url = mcpUrl(origin);
	const auth = `Bearer ${token}`;
	return [
		{
			key: "claude-code",
			label: "Claude Code",
			where: "Im Terminal ausführen:",
			text: `claude mcp add --transport http ${MCP_SERVER_NAME} ${url} --header "Authorization: ${auth}"`,
		},
		{
			key: "claude-desktop",
			label: "Claude Desktop",
			where: "In claude_desktop_config.json eintragen (Einstellungen → Entwickler → Konfiguration bearbeiten), danach Claude Desktop neu starten:",
			text: JSON.stringify({
				mcpServers: {
					[MCP_SERVER_NAME]: {
						command: "npx",
						args: ["-y", "mcp-remote", url, "--header", "Authorization:${RESONA_AUTH}"],
						env: { RESONA_AUTH: auth },
					},
				},
			}, null, 2),
		},
		{
			key: "cursor",
			label: "Cursor",
			where: "In ~/.cursor/mcp.json eintragen:",
			text: JSON.stringify({
				mcpServers: {
					[MCP_SERVER_NAME]: { url, headers: { Authorization: auth } },
				},
			}, null, 2),
		},
	];
}
