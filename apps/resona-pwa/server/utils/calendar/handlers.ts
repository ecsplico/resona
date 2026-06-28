// Calendar request handlers, dispatched from the single provider-agnostic
// `/api/calendar` route (provider via `?provider=`). Everything funnels through
// one route path on purpose: Nitro's typed `$fetch` recurses over the route
// union, so additional paths can push unrelated bare-string `$fetch` calls into
// a TS "excessive stack depth" error. One path keeps that surface minimal and
// means new providers add zero routes — only a registry entry.
import type { CalendarListView, CalendarView } from "#shared/calendar";
import { getCalendarProvider } from "./registry";
import {
	calendarSession,
	isProviderConfigured,
	oauthConfig,
	readTokens,
	requireAccessToken,
	writeTokens,
} from "./session";
import type { H3Event } from "h3";

/** Provider from `?provider=`, defaulting to google. */
export function providerFromQuery(event: H3Event): string {
	const value = getQuery(event).provider;
	return typeof value === "string" && value ? value : "google";
}

/** Start the OAuth flow: stash `<provider>:<nonce>` state, redirect to consent. */
export async function handleConnect(event: H3Event, name: string): Promise<void> {
	const provider = getCalendarProvider(name);
	const config = oauthConfig(event, provider.name);

	const state = `${provider.name}:${globalThis.crypto.randomUUID()}`;
	const session = await calendarSession(event);
	await session.update({ oauthState: state });

	await sendRedirect(event, provider.buildAuthUrl(config, state));
}

/** OAuth redirect target: verify state, derive provider from it, store tokens. */
export async function handleCallback(event: H3Event): Promise<void> {
	const query = getQuery(event);

	if (query.error) {
		return sendRedirect(event, `/calendar?error=${encodeURIComponent(String(query.error))}`);
	}

	const session = await calendarSession(event);
	const code = typeof query.code === "string" ? query.code : "";
	const state = typeof query.state === "string" ? query.state : "";
	if (!code || !state || state !== session.data.oauthState) {
		await session.update({ oauthState: undefined });
		return sendRedirect(event, "/calendar?error=invalid_state");
	}
	await session.update({ oauthState: undefined });

	// Provider name is the prefix of the state we issued at connect time.
	const name = state.split(":")[0] ?? "";
	let provider;
	try {
		provider = getCalendarProvider(name);
	}
	catch {
		return sendRedirect(event, "/calendar?error=invalid_state");
	}

	try {
		const config = oauthConfig(event, provider.name);
		const tokens = await provider.exchangeCode(config, code);
		if (!tokens.refreshToken) {
			tokens.refreshToken = session.data.tokens?.[provider.name]?.refreshToken;
		}
		await writeTokens(event, provider.name, tokens);
		return sendRedirect(event, "/calendar?connected=1");
	}
	catch {
		return sendRedirect(event, "/calendar?error=exchange_failed");
	}
}

/** Connection status + matching events in one payload; never throws on state. */
export async function handleEvents(event: H3Event, name: string): Promise<CalendarView> {
	const provider = getCalendarProvider(name);
	const configured = isProviderConfigured(event, provider.name);

	let tokens;
	try {
		tokens = await readTokens(event, provider.name);
	}
	catch {
		tokens = undefined;
	}

	const view: CalendarView = {
		provider: provider.name,
		configured,
		connected: Boolean(tokens?.refreshToken),
		email: tokens?.email,
		events: [],
	};
	if (!view.connected || !configured) {
		return view;
	}

	let accessToken: string;
	try {
		accessToken = await requireAccessToken(event, provider);
	}
	catch {
		return { ...view, connected: false };
	}

	try {
		const query = getQuery(event);
		view.events = await provider.listEvents(accessToken, {
			timeMin: typeof query.timeMin === "string" ? query.timeMin : undefined,
			timeMax: typeof query.timeMax === "string" ? query.timeMax : undefined,
			query: typeof query.q === "string" ? query.q : undefined,
			calendarId: typeof query.calendarId === "string" ? query.calendarId : undefined,
		});
	}
	catch {
		view.error = "load_failed";
	}
	return view;
}

/** List the account's calendars for the settings picker; never throws on state. */
export async function handleCalendars(event: H3Event, name: string): Promise<CalendarListView> {
	const provider = getCalendarProvider(name);
	const configured = isProviderConfigured(event, provider.name);

	let tokens;
	try {
		tokens = await readTokens(event, provider.name);
	}
	catch {
		tokens = undefined;
	}

	const connected = Boolean(tokens?.refreshToken);
	const view: CalendarListView = { configured, connected, calendars: [] };
	if (!connected || !configured || !provider.listCalendars) {
		return view;
	}

	try {
		const accessToken = await requireAccessToken(event, provider);
		view.calendars = await provider.listCalendars(accessToken);
	}
	catch {
		// Leave calendars empty — the picker falls back to the primary calendar.
	}
	return view;
}

/** Drop the stored connection and best-effort revoke the token. */
export async function handleDisconnect(event: H3Event, name: string): Promise<{ ok: true }> {
	const provider = getCalendarProvider(name);

	const tokens = await readTokens(event, provider.name);
	await writeTokens(event, provider.name, undefined);

	const token = tokens?.refreshToken ?? tokens?.accessToken;
	if (token && provider.revoke) {
		try {
			await provider.revoke(token);
		}
		catch {
			// Revocation is best-effort; the local session is already cleared.
		}
	}

	return { ok: true };
}
