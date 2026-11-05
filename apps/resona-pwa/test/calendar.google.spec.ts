import { describe, it, expect } from "vitest";
import { googleAuthUrl, googleEventParams, normalizeGoogleCalendar, normalizeGoogleEvent } from "~~/server/utils/calendar/google";

const config = {
	clientId: "cid",
	clientSecret: "secret",
	redirectUri: "http://localhost:7701/api/calendar/google/callback",
};

describe("googleAuthUrl", () => {
	it("builds a consent URL requesting an offline refresh token", () => {
		const url = new URL(googleAuthUrl(config, "state-123"));
		expect(url.origin + url.pathname).toBe("https://accounts.google.com/o/oauth2/v2/auth");
		const p = url.searchParams;
		expect(p.get("client_id")).toBe("cid");
		expect(p.get("redirect_uri")).toBe(config.redirectUri);
		expect(p.get("response_type")).toBe("code");
		expect(p.get("access_type")).toBe("offline");
		expect(p.get("prompt")).toBe("consent");
		expect(p.get("state")).toBe("state-123");
		expect(p.get("scope")).toContain("calendar.readonly");
	});
});

describe("googleEventParams", () => {
	it("always requests expanded, time-ordered single events", () => {
		const params = googleEventParams({});
		expect(params.singleEvents).toBe("true");
		expect(params.orderBy).toBe("startTime");
		expect(params.timeMin).toBeDefined();
	});

	it("passes through timeMax and query when set", () => {
		const params = googleEventParams({ timeMin: "A", timeMax: "B", query: "ct" });
		expect(params.timeMin).toBe("A");
		expect(params.timeMax).toBe("B");
		expect(params.q).toBe("ct");
	});
});

describe("normalizeGoogleEvent", () => {
	it("normalizes a timed event", () => {
		const out = normalizeGoogleEvent({
			id: "e1",
			summary: "Visite",
			location: "Raum 3",
			htmlLink: "https://cal/e1",
			start: { dateTime: "2026-06-27T09:30:00+02:00" },
			end: { dateTime: "2026-06-27T10:00:00+02:00" },
		});
		expect(out).toMatchObject({
			id: "e1",
			title: "Visite",
			location: "Raum 3",
			htmlLink: "https://cal/e1",
			allDay: false,
			start: "2026-06-27T09:30:00+02:00",
		});
	});

	it("flags all-day events and titles untitled ones", () => {
		const out = normalizeGoogleEvent({
			id: "e2",
			start: { date: "2026-06-27" },
			end: { date: "2026-06-28" },
		});
		expect(out.allDay).toBe(true);
		expect(out.start).toBe("2026-06-27");
		expect(out.title).toBe("(ohne Titel)");
	});
});

describe("normalizeGoogleCalendar", () => {
	it("uses summaryOverride before summary and flags primary", () => {
		expect(normalizeGoogleCalendar({ id: "c1", summary: "Arbeit", summaryOverride: "Praxis", primary: true }))
			.toEqual({ id: "c1", summary: "Praxis", primary: true });
	});

	it("falls back to the id when unnamed", () => {
		expect(normalizeGoogleCalendar({ id: "team@group.calendar.google.com" }))
			.toEqual({ id: "team@group.calendar.google.com", summary: "team@group.calendar.google.com", primary: false });
	});
});
