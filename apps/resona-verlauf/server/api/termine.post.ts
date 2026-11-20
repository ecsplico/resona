// POST /api/termine { action: "decline" | "undecline", event: "<id>" } → { event, declined }
// "Kein Diktat": no dictation is expected for this appointment anymore. Stores the
// event id with who/when only (never a title) in Nitro storage `verlauf`.
import { isEventId } from "~~/shared/termine";
import type { TerminAction } from "~~/shared/termine";
import { requestToken, requireVerlaufUser } from "../utils/verlauf/auth";
import { verlaufConfig } from "../utils/verlauf/config";
import { writeDecline } from "../utils/verlauf/tokenStore";

export default defineEventHandler(async (event) => {
	const config = verlaufConfig(event);
	const user = await requireVerlaufUser(config, requestToken(event, false));
	const body = await readBody<Partial<TerminAction> | null>(event);
	if (!body || (body.action !== "decline" && body.action !== "undecline") || !isEventId(body.event)) {
		throw createError({ statusCode: 400, statusMessage: "Erwartet { action: \"decline\" | \"undecline\", event: \"<Termin-ID>\" }." });
	}
	const declined = body.action === "decline";
	await writeDecline(config, body.event, declined, user.email);
	setResponseHeader(event, "Cache-Control", "no-store");
	return { event: body.event, declined };
});
