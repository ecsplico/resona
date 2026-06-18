import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref } from "vue";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import IndexPage from "~/pages/index.vue";
import type { Recording } from "~/types/directus";

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

	it("shows the empty state when there are no recordings", async () => {
		items.value = [];
		const w = await mountSuspended(IndexPage);
		expect(w.text()).toContain("Noch keine Aufnahmen");
	});
});
