import { describe, it, expect, vi, beforeEach } from "vitest";
import { defineComponent, h, reactive, ref } from "vue";
import type { Component } from "vue";
import { flushPromises } from "@vue/test-utils";
import type { VueWrapper } from "@vue/test-utils";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import IndexPage from "~/pages/index.vue";
import RecordingsPane from "~/components/RecordingsPane.vue";
import type { Recording } from "~/types/directus";
import { useHubSettings } from "~/composables/useHubSettings";
import { provideShell, useRecordingsRefresh, useSettingsDrawer } from "~/composables/useShell";

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
const remove = vi.hoisted(() => vi.fn());

// `remove` follows the composable's contract: delete through the store, then drop the row.
mockNuxtImport("useRecordings", () => () => {
	const rows = ref(items.value);
	return {
		items: rows,
		pending: ref(pending.value),
		error: ref(error.value),
		list,
		refresh,
		remove: async (id: string) => {
			await remove(id);
			await store.deleteRecording(id);
			rows.value = rows.value.filter(r => r.id !== id);
		},
	};
});

const navigateTo = vi.hoisted(() => vi.fn());
mockNuxtImport("navigateTo", () => navigateTo);

const store = vi.hoisted(() => ({ getTranscript: vi.fn(), deleteRecording: vi.fn() }));
mockNuxtImport("useDataStore", () => () => store);

// The page reads the offline queue on mount; the test env has no IndexedDB, so
// stub the composable to return an empty queue and a no-op autosync.
mockNuxtImport("useOfflineQueue", () => () => ({
	pending: vi.fn().mockResolvedValue([]),
	startAutoSync: vi.fn(),
	pendingCount: ref(0),
}));

const route = vi.hoisted(() => ({ path: "/" }));
mockNuxtImport("useRoute", () => () => reactive(route));

const saveRecording = vi.hoisted(() => vi.fn());
mockNuxtImport("useUpload", () => () => ({ saveRecording, uploading: ref(false), error: ref(null) }));

mockNuxtImport("useProfiles", () => () => ({ list: vi.fn(async () => ["default"]) }));

/** Mounts `child` under the shell the default layout provides, at the given width. */
function inShell(child: Component, wide: boolean): Component {
	return defineComponent({
		setup() {
			provideShell(ref(wide));
			return () => h(child);
		},
	});
}

/** Picks a file in the hidden upload input and saves it through the save sheet. */
async function saveUploadedFile(w: VueWrapper): Promise<void> {
	const input = w.find("[data-testid=\"upload-input\"]");
	Object.defineProperty(input.element, "files", {
		value: [new File(["audio"], "Diktat.webm", { type: "audio/webm" })],
		configurable: true,
	});
	await input.trigger("change");
	w.findComponent({ name: "SaveSheet" }).vm.$emit("save", { title: "Diktat", language: "de", profile: "default", transcribe: true });
	await flushPromises();
}

describe("index (recordings list) page", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		clearNuxtState([...LIST_STATE_KEYS]);
		items.value = [recA, recB];
		pending.value = false;
		error.value = null;
		route.path = "/";
		saveRecording.mockResolvedValue({ ...recA, id: "rec-new", title: "Diktat" });
	});

	it("shows the recordings pane on phones and the empty detail when the layout holds the pane", async () => {
		const phone = await mountSuspended(IndexPage);
		expect(phone.findComponent({ name: "RecordingsPane" }).exists()).toBe(true);
		expect(phone.find("[data-testid=\"detail-empty\"]").exists()).toBe(false);

		const desk = await mountSuspended(inShell(IndexPage, true));
		expect(desk.findComponent({ name: "RecordingsPane" }).exists()).toBe(false);
		const empty = desk.find("[data-testid=\"detail-empty\"]");
		expect(empty.text()).toContain("Wähle links eine Aufnahme aus");
		expect(empty.text()).toContain("Aufnehmen");
		expect(empty.text()).toContain("hochladen");
		expect(empty.text()).toContain("Live");
	});

	it("highlights the row of the recording open in the right pane", async () => {
		route.path = "/recording/rec-b";
		const w = await mountSuspended(inShell(RecordingsPane, true));
		const rows = w.findAllComponents({ name: "RecordingListItem" });
		expect(rows.map(r => r.props("selected"))).toEqual([false, true]);

		// Navigate within the workspace: same pane instance, selection follows the route.
		reactive(route).path = "/recording/rec-a";
		await w.vm.$nextTick();
		expect(rows.map(r => r.props("selected"))).toEqual([true, false]);
	});

	it("opens a newly saved recording in the right pane on desktop", async () => {
		const w = await mountSuspended(inShell(RecordingsPane, true));
		await saveUploadedFile(w);
		expect(saveRecording).toHaveBeenCalledWith(expect.objectContaining({ title: "Diktat" }));
		expect(refresh).toHaveBeenCalled();
		expect(navigateTo).toHaveBeenCalledWith("/recording/rec-new");
	});

	it("reloads the list when another view requests a refresh", async () => {
		const w = await mountSuspended(inShell(RecordingsPane, true));
		expect(refresh).not.toHaveBeenCalled();
		useRecordingsRefresh().request();
		await w.vm.$nextTick();
		// Panes mounted by earlier tests in this file listen too, so only presence is checked.
		expect(refresh).toHaveBeenCalled();
	});

	it("stays on the list after saving a new recording on phones", async () => {
		const w = await mountSuspended(IndexPage);
		await saveUploadedFile(w);
		expect(saveRecording).toHaveBeenCalled();
		expect(refresh).toHaveBeenCalled();
		expect(navigateTo).not.toHaveBeenCalled();
		expect(w.findComponent({ name: "SaveSheet" }).exists()).toBe(false);
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
		useHubSettings().token = "tok";
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

	it("swaps the history entry when another recording is opened from the workspace list", async () => {
		route.path = "/recording/rec-b";
		const w = await mountSuspended(inShell(RecordingsPane, true));
		w.findAllComponents({ name: "RecordingListItem" })[0]!.vm.$emit("open");
		await w.vm.$nextTick();
		expect(navigateTo).toHaveBeenCalledWith("/recording/rec-a", { replace: true });
	});

	it("keeps search text and date filter when the list is mounted again", async () => {
		const first = await mountSuspended(IndexPage);
		await first.find("[data-testid=\"search\"]").setValue("Visite");
		await first.find("[data-testid=\"since\"]").setValue("2026-06-01");
		first.unmount();

		list.mockClear();
		const again = await mountSuspended(IndexPage);
		expect((again.find("[data-testid=\"search\"]").element as HTMLInputElement).value).toBe("Visite");
		expect((again.find("[data-testid=\"since\"]").element as HTMLInputElement).value).toBe("2026-06-01");
		expect(list).toHaveBeenCalledWith({ search: "Visite", since: "2026-06-01" });
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

	it("opens the row menu without opening the recording", async () => {
		const w = await mountSuspended(IndexPage, { attachTo: document.body });
		const row = w.findAllComponents({ name: "RecordingListItem" })[0]!;
		expect(row.find("[data-testid=\"copy-action\"]").exists()).toBe(true);
		await row.find("[aria-haspopup=\"menu\"]").trigger("click");
		await flushPromises();
		expect(row.find("[role=\"menu\"]").exists()).toBe(true);
		await row.find("[data-testid=\"menu-delete\"]").trigger("click");
		await flushPromises();
		expect(navigateTo).not.toHaveBeenCalled();
		w.unmount();
	});

	it("deletes a recording only after confirming, through the store, and drops its row", async () => {
		store.deleteRecording.mockResolvedValue(undefined);
		const w = await mountSuspended(IndexPage, { attachTo: document.body });
		const row = w.findAllComponents({ name: "RecordingListItem" })[0]!;
		await row.find("[aria-haspopup=\"menu\"]").trigger("click");
		await flushPromises();
		await row.find("[data-testid=\"menu-delete\"]").trigger("click");
		await flushPromises();

		const dialog = w.find("[data-testid=\"delete-dialog\"]");
		expect(dialog.attributes("role")).toBe("alertdialog");
		expect(dialog.text()).toContain("Aufnahme löschen?");
		expect(dialog.text()).toContain("„Visite Station 3“");
		expect(store.deleteRecording).not.toHaveBeenCalled();

		await dialog.find("[data-testid=\"confirm-delete\"]").trigger("click");
		await flushPromises();

		expect(remove).toHaveBeenCalledWith("rec-a");
		expect(store.deleteRecording).toHaveBeenCalledWith("rec-a");
		expect(w.find("[data-testid=\"delete-dialog\"]").exists()).toBe(false);
		expect(w.text()).not.toContain("Visite Station 3");
		expect(w.findAllComponents({ name: "RecordingListItem" }).length).toBe(1);
		expect(navigateTo).not.toHaveBeenCalled();
		w.unmount();
	});

	it("keeps the recording when the delete is cancelled", async () => {
		const w = await mountSuspended(IndexPage, { attachTo: document.body });
		const row = w.findAllComponents({ name: "RecordingListItem" })[0]!;
		await row.find("[aria-haspopup=\"menu\"]").trigger("click");
		await flushPromises();
		await row.find("[data-testid=\"menu-delete\"]").trigger("click");
		await flushPromises();
		await w.find("[data-testid=\"cancel-delete\"]").trigger("click");

		expect(w.find("[data-testid=\"delete-dialog\"]").exists()).toBe(false);
		expect(remove).not.toHaveBeenCalled();
		expect(w.findAllComponents({ name: "RecordingListItem" }).length).toBe(2);
		w.unmount();
	});

	it("shows why a delete failed and keeps the row", async () => {
		remove.mockRejectedValueOnce(new Error("Keine Berechtigung."));
		const w = await mountSuspended(IndexPage);
		w.findAllComponents({ name: "RecordingListItem" })[0]!.vm.$emit("action", "delete");
		await flushPromises();
		await w.find("[data-testid=\"confirm-delete\"]").trigger("click");
		await flushPromises();

		expect(w.find("[data-testid=\"delete-error\"]").exists()).toBe(true);
		expect(store.deleteRecording).not.toHaveBeenCalled();
		expect(w.findAllComponents({ name: "RecordingListItem" }).length).toBe(2);
	});

	it("leaves the right pane before deleting the recording open there", async () => {
		route.path = "/recording/rec-a";
		store.deleteRecording.mockResolvedValue(undefined);
		const order: string[] = [];
		navigateTo.mockImplementationOnce(async () => {
			order.push("navigate");
		});
		remove.mockImplementationOnce(async () => {
			order.push("delete");
		});
		const w = await mountSuspended(inShell(RecordingsPane, true));
		w.findAllComponents({ name: "RecordingListItem" })[0]!.vm.$emit("action", "delete");
		await flushPromises();
		await w.find("[data-testid=\"confirm-delete\"]").trigger("click");
		await flushPromises();
		expect(navigateTo).toHaveBeenCalledWith("/", { replace: true });
		expect(order).toEqual(["navigate", "delete"]);
	});

	it("keeps the recording open there when its page refuses to be left", async () => {
		route.path = "/recording/rec-a";
		navigateTo.mockResolvedValueOnce(false);
		const w = await mountSuspended(inShell(RecordingsPane, true));
		w.findAllComponents({ name: "RecordingListItem" })[0]!.vm.$emit("action", "delete");
		await flushPromises();
		await w.find("[data-testid=\"confirm-delete\"]").trigger("click");
		await flushPromises();
		expect(navigateTo).toHaveBeenCalledWith("/", { replace: true });
		expect(remove).not.toHaveBeenCalled();
		expect(w.find("[data-testid=\"delete-dialog\"]").exists()).toBe(false);
		expect(w.findAllComponents({ name: "RecordingListItem" }).length).toBe(2);
	});

	it("keeps focus inside the delete dialog, closes it with Escape and returns focus to the trigger", async () => {
		const w = await mountSuspended(IndexPage, { attachTo: document.body });
		const row = w.findAllComponents({ name: "RecordingListItem" })[0]!;
		const trigger = row.find("[aria-haspopup=\"menu\"]");
		await trigger.trigger("click");
		await flushPromises();
		await row.find("[data-testid=\"menu-delete\"]").trigger("click");
		await flushPromises();

		const dialog = w.find("[data-testid=\"delete-dialog\"]");
		const cancel = dialog.find("[data-testid=\"cancel-delete\"]").element as HTMLElement;
		const confirm = dialog.find("[data-testid=\"confirm-delete\"]").element as HTMLElement;
		expect(document.activeElement).toBe(cancel);

		await dialog.trigger("keydown", { key: "Tab", shiftKey: true });
		expect(document.activeElement).toBe(confirm);
		await dialog.trigger("keydown", { key: "Tab" });
		expect(document.activeElement).toBe(cancel);

		await dialog.trigger("keydown", { key: "Escape" });
		expect(w.find("[data-testid=\"delete-dialog\"]").exists()).toBe(false);
		expect(document.activeElement).toBe(trigger.element);
		expect(remove).not.toHaveBeenCalled();
		w.unmount();
	});

	it("prefetches the transcript when the row menu opens and shares it without an await gap", async () => {
		const share = vi.fn(async () => {});
		Object.defineProperty(globalThis.navigator, "share", { value: share, configurable: true });
		store.getTranscript.mockResolvedValueOnce({ id: "t-a", recording: "rec-a", text: "Der Befund ist unauffällig." });
		try {
			const w = await mountSuspended(IndexPage);
			const row = w.findAllComponents({ name: "RecordingListItem" })[0]!;
			row.vm.$emit("menu-open");
			await flushPromises();
			expect(store.getTranscript).toHaveBeenCalledWith("rec-a");

			// Synchronous: share runs inside the same tick as the tap (iOS user activation).
			row.vm.$emit("action", "share");
			expect(share).toHaveBeenCalledWith({ title: "Visite Station 3", text: "Visite Station 3\n\nDer Befund ist unauffällig." });
		}
		finally {
			Reflect.deleteProperty(globalThis.navigator, "share");
		}
	});

	it("asks to tap again instead of awaiting a transcript that is not loaded yet", async () => {
		const share = vi.fn(async () => {});
		Object.defineProperty(globalThis.navigator, "share", { value: share, configurable: true });
		store.getTranscript.mockResolvedValueOnce({ id: "t-a", recording: "rec-a", text: "Der Befund ist unauffällig." });
		try {
			const w = await mountSuspended(IndexPage);
			const row = w.findAllComponents({ name: "RecordingListItem" })[0]!;
			row.vm.$emit("action", "share");
			await flushPromises();
			expect(share).not.toHaveBeenCalled();
			expect(w.find("[data-testid=\"action-notice\"]").text()).toBe("Wird vorbereitet … bitte erneut tippen.");

			row.vm.$emit("action", "share");
			expect(share).toHaveBeenCalledTimes(1);
		}
		finally {
			Reflect.deleteProperty(globalThis.navigator, "share");
		}
	});

	it("copies the text instead when the browser cannot share", async () => {
		const writeText = vi.fn(async () => {});
		Object.defineProperty(globalThis.navigator, "clipboard", { value: { writeText }, configurable: true });
		const original = Object.getOwnPropertyDescriptor(globalThis.navigator, "share");
		Reflect.deleteProperty(globalThis.navigator, "share");
		try {
			const w = await mountSuspended(IndexPage);
			w.findAllComponents({ name: "RecordingListItem" })[1]!.vm.$emit("action", "share");
			await flushPromises();
			expect(writeText).toHaveBeenCalledWith("Telefonat Reha");
			expect(w.find("[data-testid=\"action-notice\"]").text()).toBe("Teilen wird nicht unterstützt – Text kopiert.");
		}
		finally {
			if (original) Object.defineProperty(globalThis.navigator, "share", original);
		}
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

	it("refuses to record without a hub token and names Einstellungen → Verbindung", async () => {
		useHubSettings().token = "";
		const getUserMedia = vi.fn();
		const original = Object.getOwnPropertyDescriptor(globalThis.navigator, "mediaDevices");
		Object.defineProperty(globalThis.navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
		try {
			const w = await mountSuspended(IndexPage);
			await w.find("[data-testid=\"record-fab\"]").trigger("click");
			await flushPromises();
			expect(getUserMedia).not.toHaveBeenCalled();
			expect(w.findComponent({ name: "HRecordingSheet" }).exists()).toBe(false);
			expect(w.find("[data-testid=\"record-error\"]").text()).toContain("Einstellungen → Verbindung");
		}
		finally {
			if (original) Object.defineProperty(globalThis.navigator, "mediaDevices", original);
			else Reflect.deleteProperty(globalThis.navigator, "mediaDevices");
		}
	});

	it("refuses to open the file picker without a hub token and opens it with one", async () => {
		const click = vi.spyOn(HTMLInputElement.prototype, "click").mockImplementation(() => {});
		try {
			useHubSettings().token = "";
			const w = await mountSuspended(IndexPage);
			await w.find("[data-testid=\"upload-trigger\"]").trigger("click");
			expect(click).not.toHaveBeenCalled();
			expect(w.find("[data-testid=\"record-error\"]").text()).toContain("Einstellungen → Verbindung");

			useHubSettings().token = "tok";
			await w.find("[data-testid=\"upload-trigger\"]").trigger("click");
			expect(click).toHaveBeenCalledTimes(1);
			expect(w.find("[data-testid=\"record-error\"]").exists()).toBe(false);
		}
		finally {
			click.mockRestore();
			useHubSettings().token = "";
		}
	});
});
