import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref } from "vue";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import IndexPage from "~/pages/index.vue";
import type { Recording } from "~/types/directus";
import type { QueuedItem } from "~/composables/useOfflineQueue";

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

const items = vi.hoisted(() => ({ value: [] as Recording[] }));
const list = vi.hoisted(() => vi.fn(async () => []));
const refresh = vi.hoisted(() => vi.fn(async () => []));

mockNuxtImport("useRecordings", () => () => ({
	items: ref(items.value),
	pending: ref(false),
	error: ref(null as string | null),
	list,
	refresh,
}));

mockNuxtImport("navigateTo", () => vi.fn());

const queuedItem: QueuedItem = {
	id: "q-1",
	title: "Notiz unterwegs",
	language: "de",
	profile: "default",
	transcribe: true,
	duration_seconds: 7,
	blob: new Blob(["audio"], { type: "audio/webm" }),
	createdAt: Date.now(),
};

const pendingFn = vi.hoisted(() => vi.fn());
mockNuxtImport("useOfflineQueue", () => () => ({
	pending: pendingFn,
	startAutoSync: vi.fn(),
	pendingCount: ref(1),
}));

describe("index page — offline queue section", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		items.value = [recA];
		pendingFn.mockResolvedValue([queuedItem]);
	});

	it("renders queued offline items above the server list with a 'Wartet (offline)' label", async () => {
		const w = await mountSuspended(IndexPage);
		await w.vm.$nextTick();

		const queuedList = w.find("[data-testid=\"queued-list\"]");
		expect(queuedList.exists()).toBe(true);

		const rows = w.findAll("[data-testid=\"queued-item\"]");
		expect(rows.length).toBe(1);
		expect(w.text()).toContain("Notiz unterwegs");
		expect(w.text()).toContain("Wartet (offline)");
		// Still shows the server recording too.
		expect(w.text()).toContain("Visite Station 3");
	});
});
