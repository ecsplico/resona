import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useCalendar } from "~/composables/useCalendar";
import { useCalendarSettings } from "~/composables/useCalendarSettings";

// $fetch is a Nuxt global — stub it directly (same approach as useProfiles.spec).
const fetchMock = vi.fn();
const realFetch = (globalThis as Record<string, unknown>).$fetch;

describe("useCalendar", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		(globalThis as Record<string, unknown>).$fetch = fetchMock;
	});

	afterEach(() => {
		(globalThis as Record<string, unknown>).$fetch = realFetch;
	});

	it("exposes a per-provider connect URL", () => {
		expect(useCalendar("google").connectUrl).toBe("/api/calendar?provider=google&action=connect");
		expect(useCalendar("outlook").connectUrl).toBe("/api/calendar?provider=outlook&action=connect");
	});

	it("refresh sends the date window + query and stores status and events", async () => {
		fetchMock.mockResolvedValue({
			provider: "google",
			configured: true,
			connected: true,
			email: "a@b.de",
			events: [{ id: "1", title: "Visite", start: "2026-06-27", end: "2026-06-27", allDay: true }],
		});
		const cal = useCalendar();
		cal.filter.from = "2026-06-27";
		cal.filter.to = "2026-06-27";
		cal.filter.query = "ct";
		await cal.refresh();

		const [url, opts] = fetchMock.mock.calls[0]!;
		expect(url).toBe("/api/calendar");
		const query = (opts as { query: { provider: string; q: string; timeMin: string; timeMax: string } }).query;
		expect(query.provider).toBe("google");
		expect(query.q).toBe("ct");
		expect(query.timeMin).toBeTruthy();
		expect(query.timeMax).toBeTruthy();
		expect(cal.status.value?.connected).toBe(true);
		expect(cal.events.value).toHaveLength(1);
	});

	it("refresh surfaces a load error from the view payload", async () => {
		fetchMock.mockResolvedValue({ provider: "google", configured: true, connected: true, events: [], error: "load_failed" });
		const cal = useCalendar();
		await cal.refresh();
		expect(cal.error.value).toBeTruthy();
		expect(cal.events.value).toHaveLength(0);
	});

	it("refresh degrades to not-reachable on a thrown error", async () => {
		fetchMock.mockRejectedValue(new Error("boom"));
		const cal = useCalendar();
		await cal.refresh();
		expect(cal.status.value).toMatchObject({ connected: false, configured: false });
		expect(cal.error.value).toBeTruthy();
	});

	it("sends the selected calendarId from settings", async () => {
		useCalendarSettings().calendarId = "team@group.calendar.google.com";
		fetchMock.mockResolvedValue({ provider: "google", configured: true, connected: true, events: [] });
		const cal = useCalendar();
		await cal.refresh();
		const [, opts] = fetchMock.mock.calls[0]!;
		expect((opts as { query: { calendarId?: string } }).query.calendarId).toBe("team@group.calendar.google.com");
		useCalendarSettings().calendarId = ""; // restore for other tests
	});

	it("listCalendars returns the account calendars and updates status", async () => {
		fetchMock.mockResolvedValue({
			configured: true,
			connected: true,
			calendars: [{ id: "primary", summary: "Ich", primary: true }],
		});
		const cal = useCalendar();
		const calendars = await cal.listCalendars();
		expect(fetchMock).toHaveBeenCalledWith("/api/calendar", { query: { provider: "google", action: "calendars" } });
		expect(calendars).toHaveLength(1);
		expect(cal.status.value).toMatchObject({ configured: true, connected: true });
	});

	it("disconnect DELETEs the connection then refreshes", async () => {
		fetchMock
			.mockResolvedValueOnce({ ok: true })
			.mockResolvedValueOnce({ provider: "google", configured: true, connected: false, events: [] });
		const cal = useCalendar();
		cal.events.value = [{ id: "1", title: "x", start: "", end: "", allDay: false }];
		await cal.disconnect();

		expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/calendar", { method: "DELETE", query: { provider: "google" } });
		expect(fetchMock.mock.calls[1]![0]).toBe("/api/calendar");
		expect(cal.events.value).toHaveLength(0);
		expect(cal.status.value?.connected).toBe(false);
	});

	it("resetFilter re-seeds the window and query from changed settings", () => {
		const settings = useCalendarSettings();
		const saved = { daysBack: settings.daysBack, daysAhead: settings.daysAhead, query: settings.query };
		const cal = useCalendar();
		cal.filter.from = "2020-01-01";
		settings.daysBack = 0;
		settings.daysAhead = 1;
		settings.query = "Visite";
		cal.resetFilter();

		const day = (offset: number) => {
			const d = new Date();
			d.setDate(d.getDate() + offset);
			return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
		};
		expect(cal.filter.from).toBe(day(0));
		expect(cal.filter.to).toBe(day(1));
		expect(cal.filter.query).toBe("Visite");
		Object.assign(settings, saved);
	});
});
