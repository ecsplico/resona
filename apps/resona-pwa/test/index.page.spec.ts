import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref } from "vue";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import IndexPage from "~/pages/index.vue";
import type { Recording } from "~/types/directus";
import { useHubSettings } from "~/composables/useHubSettings";

const recA: Recording = {
	id: "rec-a",
	title: "Visite Station 3",
	date_created: "2026-06-19T08:30:00.000Z",
	duration_seconds: 42,
	language: "de",
	profile: "default",
	status: "done",
	source: "batch",
};
const recB: Recording = {
	id: "rec-b",
	title: "Telefonat Reha",
	date_created: "2026-06-18T14:05:00.000Z",
	duration_seconds: 130,
	language: "de",
	profile: "default",
	status: "pending",
	source: "live",
};

const items = vi.hoisted(() => ({ value: [] as Recording[] }));
const pending = vi.hoisted(() => ({ value: false }));
const error = vi.hoisted(() => ({ value: null as string | null }));
const list = vi.hoisted(() => vi.fn(async () => []));
const refresh = vi.hoisted(() => vi.fn(async () => []));

mockNuxtImport("useRecordings", () => () => ({
	items: ref(items.value),
	pending: ref(pending.value),
	error: ref(error.value),
	list,
	refresh,
}));

const navigateTo = vi.hoisted(() => vi.fn());
mockNuxtImport("navigateTo", () => navigateTo);

const request = vi.hoisted(() => vi.fn());
mockNuxtImport("useDirectus", () => () => ({ client: { request } }));

// The page reads the offline queue on mount; the test env has no IndexedDB, so
// stub the composable to return an empty queue and a no-op autosync.
mockNuxtImport("useOfflineQueue", () => () => ({
	pending: vi.fn().mockResolvedValue([]),
	startAutoSync: vi.fn(),
	pendingCount: ref(0),
}));

describe("index (recordings list) page", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		items.value = [recA, recB];
		pending.value = false;
		error.value = null;
	});

	it("renders a list item per recording with title and status", async () => {
		const w = await mountSuspended(IndexPage);
		const rows = w.findAllComponents({ name: "RecordingListItem" });
		expect(rows.length).toBe(2);
		expect(w.text()).toContain("Visite Station 3");
		expect(w.text()).toContain("Telefonat Reha");
		expect(w.text()).toContain("Fertig");
		expect(w.text()).toContain("Wartet");
	});

	it("renders a search field", async () => {
		const w = await mountSuspended(IndexPage);
		expect(w.findComponent({ name: "HField" }).exists()).toBe(true);
	});

	it("renders the record FAB", async () => {
		const w = await mountSuspended(IndexPage);
		expect(w.find("[data-testid=\"record-fab\"]").exists()).toBe(true);
	});

	it("navigates to the recording detail when a row is opened", async () => {
		const w = await mountSuspended(IndexPage);
		const row = w.findAllComponents({ name: "RecordingListItem" })[0]!;
		row.vm.$emit("open");
		await w.vm.$nextTick();
		expect(navigateTo).toHaveBeenCalledWith("/recording/rec-a");
	});

	it("copies a recording's transcript text from the list", async () => {
		const writeText = vi.fn(async () => {});
		Object.defineProperty(globalThis.navigator, "clipboard", {
			value: { writeText },
			configurable: true,
		});
		request.mockResolvedValueOnce([{ id: "t-a", recording: "rec-a", text: "Der Befund ist unauffällig." }]);

		const w = await mountSuspended(IndexPage);
		const row = w.findAllComponents({ name: "RecordingListItem" })[0]!;
		row.vm.$emit("copy");
		await w.vm.$nextTick();
		await new Promise(r => setTimeout(r, 0));

		// fetched the transcript for the done recording, then copied title + text
		expect(request).toHaveBeenCalled();
		expect(writeText).toHaveBeenCalledWith("Visite Station 3\n\nDer Befund ist unauffällig.");
	});

	it("falls back to copying the title for an untranscribed recording", async () => {
		const writeText = vi.fn(async () => {});
		Object.defineProperty(globalThis.navigator, "clipboard", {
			value: { writeText },
			configurable: true,
		});

		const w = await mountSuspended(IndexPage);
		// recB is status=pending → no transcript fetch, copy the title
		const row = w.findAllComponents({ name: "RecordingListItem" })[1]!;
		row.vm.$emit("copy");
		await w.vm.$nextTick();
		await new Promise(r => setTimeout(r, 0));

		expect(request).not.toHaveBeenCalled();
		expect(writeText).toHaveBeenCalledWith("Telefonat Reha");
	});

	it("auto-refreshes the list while a recording is still transcribing", async () => {
		vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
		try {
			items.value = [{ ...recA, status: "transcribing" }];
			const w = await mountSuspended(IndexPage);
			list.mockClear();
			vi.advanceTimersByTime(4000);
			expect(list).toHaveBeenCalled();
			w.unmount();
		}
		finally {
			vi.useRealTimers();
		}
	});

	it("does not poll the server once every recording is terminal", async () => {
		vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
		try {
			items.value = [recA]; // status=done → nothing in flight
			const w = await mountSuspended(IndexPage);
			list.mockClear();
			vi.advanceTimersByTime(8000);
			expect(list).not.toHaveBeenCalled();
			w.unmount();
		}
		finally {
			vi.useRealTimers();
		}
	});

	it("shows the empty state when there are no recordings", async () => {
		items.value = [];
		const w = await mountSuspended(IndexPage);
		expect(w.text()).toContain("Noch keine Aufnahmen");
	});

	it("keeps the rows steady without a loading hint while refreshing existing recordings", async () => {
		pending.value = true;
		const w = await mountSuspended(IndexPage);
		expect(w.text()).not.toContain("Lädt…");
		expect(w.findAllComponents({ name: "RecordingListItem" }).length).toBe(2);
	});

	it("shows the loading hint while loading with no recordings yet", async () => {
		items.value = [];
		pending.value = true;
		const w = await mountSuspended(IndexPage);
		expect(w.text()).toContain("Lädt…");
		expect(w.text()).not.toContain("Noch keine Aufnahmen");
	});

	it("shows the no-token notice only while the hub token is empty", async () => {
		useHubSettings().token = "";
		const w = await mountSuspended(IndexPage);
		expect(w.find("[data-testid=\"hub-token-missing\"]").text()).toBe("Kein Hub-Token – Transkription, Diktat und Live brauchen einen Token (Einstellungen → Verbindung).");
		useHubSettings().token = "tok";
		const w2 = await mountSuspended(IndexPage);
		expect(w2.find("[data-testid=\"hub-token-missing\"]").exists()).toBe(false);
		useHubSettings().token = "";
	});
});
