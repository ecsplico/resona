// POST /api/termine → { event, mark: "erledigt" | "abgesagt" | null }
//   { action: "mark", event: "<id>", status: "erledigt" | "abgesagt" }
//   { action: "unmark", event: "<id>" }
// A mark counts as "dictation done": the appointment is never open again. Stores the
// event id with status and who/when only (never a title) in Nitro storage `verlauf`.
import { isEventId, isTerminMark } from "~~/shared/termine";
import type { TerminMark } from "~~/shared/termine";
import { requestToken, requireVerlaufUser } from "../utils/verlauf/auth";
import { verlaufConfig } from "../utils/verlauf/config";
import { writeMark } from "../utils/verlauf/tokenStore";

export default defineEventHandler(async (event) => {
	const config = verlaufConfig(event);
	const user = await requireVerlaufUser(config, requestToken(event, false));
	const body = await readBody<{ action?: unknown; event?: unknown; status?: unknown } | null>(event);
	let mark: TerminMark | null | undefined;
	if (body?.action === "mark" && isTerminMark(body.status)) mark = body.status;
	else if (body?.action === "unmark") mark = null;
	if (mark === undefined || !isEventId(body?.event)) {
		throw createError({ statusCode: 400, statusMessage: "Erwartet { action: \"mark\", event, status: \"erledigt\" | \"abgesagt\" } oder { action: \"unmark\", event }." });
	}
	await writeMark(config, body.event, mark, user.email);
	setResponseHeader(event, "Cache-Control", "no-store");
	return { event: body.event, mark };
});
