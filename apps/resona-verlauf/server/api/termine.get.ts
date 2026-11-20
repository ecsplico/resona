// The one Verlauf route family (keep Nitro route paths minimal, see resona-pwa CLAUDE.md):
//   GET /api/termine?date=YYYY-MM-DD                → { connected, events } (Bearer Directus token)
//   GET /api/termine?from=YYYY-MM-DD&to=YYYY-MM-DD  → same, inclusive Berlin days, ≤ 200 days
//   GET /api/termine?context=<event id>             → { connected, before, after } (same patient)
//   GET /api/termine?action=connect                 → 302 to Google consent (token also as ?access_token=)
//   GET /api/termine?code=…&state=…                 → OAuth callback (redirect URI <origin>/api/termine)
//   POST /api/termine (termine.post.ts)             → marks "erledigt" / "abgesagt"
// Event titles carry patient names: nothing here logs them, and no query carries one.
import { dayBounds, isDay, isEventId, MAX_RANGE_DAYS, rangeBounds } from "~~/shared/termine";
import { requestToken, requireVerlaufUser } from "../utils/verlauf/auth";
import { verlaufConfig } from "../utils/verlauf/config";
import { handleOAuthCallback, listTermine, startConnect, termineContext } from "../utils/verlauf/google";

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
		return startConnect(event, config, user.id);
	}

	await requireVerlaufUser(config, requestToken(event, false));
	setResponseHeader(event, "Cache-Control", "no-store");

	if (query.context !== undefined) {
		if (!isEventId(query.context)) {
			throw createError({ statusCode: 400, statusMessage: "Parameter context=<Termin-ID> ist ungültig." });
		}
		return termineContext(event, config, query.context);
	}

	if (query.from !== undefined || query.to !== undefined) {
		const bounds = rangeBounds(query.from, query.to);
		if (!bounds) {
			throw createError({ statusCode: 400, statusMessage: `Parameter from/to=YYYY-MM-DD fehlen, sind ungültig oder umfassen mehr als ${MAX_RANGE_DAYS} Tage.` });
		}
		return listTermine(event, config, bounds);
	}

	if (!isDay(query.date)) {
		throw createError({ statusCode: 400, statusMessage: "Parameter date=YYYY-MM-DD fehlt oder ist ungültig." });
	}
	return listTermine(event, config, dayBounds(query.date));
});
