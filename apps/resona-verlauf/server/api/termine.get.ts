// The one Verlauf route (keep Nitro route paths minimal, see resona-pwa CLAUDE.md):
//   GET /api/termine?date=YYYY-MM-DD      → { connected, events } (Bearer Directus token)
//   GET /api/termine?action=connect       → 302 to Google consent (token also as ?access_token=)
//   GET /api/termine?code=…&state=…       → OAuth callback (redirect URI <origin>/api/termine)
// Event titles carry patient names: nothing here logs them.
import { isDay } from "~~/shared/termine";
import { requestToken, requireVerlaufUser } from "../utils/verlauf/auth";
import { verlaufConfig } from "../utils/verlauf/config";
import { handleOAuthCallback, listTermine, startConnect } from "../utils/verlauf/google";

export default defineEventHandler(async (event) => {
	const query = getQuery(event);
	const config = verlaufConfig(event);

	// Google's redirect back carries no Directus token; the sealed state proves the
	// flow was started by an allowed user in this browser.
	if (typeof query.code === "string" || typeof query.error === "string") {
		return handleOAuthCallback(event, config);
	}

	if (query.action === "connect") {
		const user = await requireVerlaufUser(config, requestToken(event, true));
		return startConnect(event, config, user);
	}

	await requireVerlaufUser(config, requestToken(event, false));
	if (!isDay(query.date)) {
		throw createError({ statusCode: 400, statusMessage: "Parameter date=YYYY-MM-DD fehlt oder ist ungültig." });
	}
	setResponseHeader(event, "Cache-Control", "no-store");
	return listTermine(event, config, query.date);
});
