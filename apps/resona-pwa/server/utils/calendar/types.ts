// Server-side calendar provider contract. Mirror of the repo's cloud-stt /
// cloud-tts provider pattern: a small interface + a registry so additional
// calendar backends (Outlook, CalDAV, …) slot in as one file each.
import type { CalendarEvent, CalendarFilter, CalendarOption, TokenSet } from "#shared/calendar";

/** Resolved OAuth client credentials for one provider. */
export interface OAuthConfig {
	clientId: string;
	clientSecret: string;
	redirectUri: string;
}

/**
 * A calendar provider. Every method is provider-specific; the routes are
 * provider-agnostic and dispatch through the registry. Implementations talk to
 * their upstream API and normalize into the shared `CalendarEvent` shape.
 */
export interface CalendarProvider {
	/** Stable provider key, e.g. `"google"`. */
	readonly name: string;
	/** OAuth scopes requested at connect time. */
	readonly scopes: readonly string[];
	/** Build the provider's OAuth consent URL to redirect the user to. */
	buildAuthUrl(config: OAuthConfig, state: string): string;
	/** Exchange an authorization `code` for an initial token set. */
	exchangeCode(config: OAuthConfig, code: string): Promise<TokenSet>;
	/** Mint a fresh access token from a stored refresh token. */
	refresh(config: OAuthConfig, refreshToken: string): Promise<TokenSet>;
	/** List events matching `filter` using a valid access token. */
	listEvents(accessToken: string, filter: CalendarFilter): Promise<CalendarEvent[]>;
	/** List the account's selectable calendars (optional — for the picker). */
	listCalendars?(accessToken: string): Promise<CalendarOption[]>;
	/** Best-effort token revocation on disconnect (optional). */
	revoke?(token: string): Promise<void>;
}
