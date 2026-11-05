import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref } from "vue";
import { flushPromises } from "@vue/test-utils";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import IndexPage from "~/pages/index.vue";
import type { Recording } from "~/types/directus";
import { useHubSettings } from "~/composables/useHubSettings";
import { useSettingsDrawer } from "~/composables/useShell";

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

const store = vi.hoisted(() => ({ getTranscript: vi.fn() }));
mockNuxtImport("useDataStore", () => () => store);

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

	it("renders the rows in one grouped list, with a chip only for rows that need attention", async () => {
		const w = await mountSuspended(IndexPage);
		const rowList = w.find("[data-testid=\"recordings-list\"]");
		expect(rowList.classes()).toContain("h-list");
		expect(rowList.findAllComponents({ name: "RecordingListItem" }).length).toBe(2);
		expect(w.text()).toContain("Visite Station 3");
		expect(w.text()).toContain("Telefonat Reha");
		expect(w.text()).toContain("Wartet");
		expect(w.text()).not.toContain("Fertig");
	});

	it("filters by search text and start date", async () => {
		const w = await mountSuspended(IndexPage);
		list.mockClear();
		const search = w.find("[data-testid=\"search\"]");
		expect(search.attributes("placeholder")).toBe("Suchen");
		await search.setValue("Visite");
		expect(list).toHaveBeenLastCalledWith({ search: "Visite", since: "" });
		await w.find("[data-testid=\"since\"]").setValue("2026-06-01");
		expect(list).toHaveBeenLastCalledWith({ search: "Visite", since: "2026-06-01" });
	});

	it("offers upload, record and live in the capture dock", async () => {
		const w = await mountSuspended(IndexPage);
		const dock = w.find("[data-testid=\"capture-dock\"]");
		expect(dock.find("[data-testid=\"upload-trigger\"]").text()).toBe("Hochladen");
		expect(dock.find("[data-testid=\"record-fab\"]").text()).toBe("Aufnehmen");
		const live = dock.find("[data-testid=\"nav-live\"]");
		expect(live.text()).toBe("Live");
		expect(live.attributes("href")).toBe("/live");
	});

	it("closes the recording sheet and shows the error when the mic cannot start", async () => {
		const original = Object.getOwnPropertyDescriptor(globalThis.navigator, "mediaDevices");
		Object.defineProperty(globalThis.navigator, "mediaDevices", { value: undefined, configurable: true });
		try {
			const w = await mountSuspended(IndexPage);
			await w.find("[data-testid=\"record-fab\"]").trigger("click");
			await flushPromises();
			expect(w.findComponent({ name: "HRecordingSheet" }).exists()).toBe(false);
			expect(w.find("[data-testid=\"record-error\"]").text()).toBe("Mikrofon nicht verfügbar – die App muss über HTTPS oder localhost geöffnet sein.");
		}
		finally {
			if (original) Object.defineProperty(globalThis.navigator, "mediaDevices", original);
			else Reflect.deleteProperty(globalThis.navigator, "mediaDevices");
		}
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
		store.getTranscript.mockResolvedValueOnce({ id: "t-a", recording: "rec-a", text: "Der Befund ist unauffällig." });

		const w = await mountSuspended(IndexPage);
		const row = w.findAllComponents({ name: "RecordingListItem" })[0]!;
		row.vm.$emit("copy");
		await w.vm.$nextTick();
		await new Promise(r => setTimeout(r, 0));

		// fetched the transcript for the done recording, then copied title + text
		expect(store.getTranscript).toHaveBeenCalledWith("rec-a");
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

		expect(store.getTranscript).not.toHaveBeenCalled();
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
		expect(w.find("[data-testid=\"list-empty\"]").text()).toBe("Noch keine Aufnahmen – nimm etwas auf oder lade eine Datei hoch.");
		expect(w.find("[data-testid=\"list-skeleton\"]").exists()).toBe(false);
	});

	it("keeps the rows steady without skeletons while refreshing existing recordings", async () => {
		pending.value = true;
		const w = await mountSuspended(IndexPage);
		expect(w.find("[data-testid=\"list-skeleton\"]").exists()).toBe(false);
		expect(w.findAllComponents({ name: "RecordingListItem" }).length).toBe(2);
	});

	it("shows three skeleton rows while loading with no recordings yet", async () => {
		items.value = [];
		pending.value = true;
		const w = await mountSuspended(IndexPage);
		expect(w.findAll("[data-testid=\"skeleton-row\"]").length).toBe(3);
		expect(w.find("[data-testid=\"list-empty\"]").exists()).toBe(false);
	});

	it("shows the no-token notice only while the hub token is empty, with a way into the settings", async () => {
		useHubSettings().token = "";
		const drawer = useSettingsDrawer();
		drawer.hide();
		const w = await mountSuspended(IndexPage);
		const notice = w.find("[data-testid=\"hub-token-missing\"]");
		expect(notice.text()).toContain("Kein Hub-Token – Transkription, Diktat und Live brauchen einen Token.");
		await notice.find("[data-testid=\"hub-token-open-settings\"]").trigger("click");
		expect(drawer.open.value).toBe(true);
		drawer.hide();
		useHubSettings().token = "tok";
		const w2 = await mountSuspended(IndexPage);
		expect(w2.find("[data-testid=\"hub-token-missing\"]").exists()).toBe(false);
		useHubSettings().token = "";
	});
});
