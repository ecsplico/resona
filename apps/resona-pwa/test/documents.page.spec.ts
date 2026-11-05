import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { flushPromises } from "@vue/test-utils";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import DocumentsPage from "~/pages/documents/index.vue";
import type { ResonaDocument } from "~/types/directus";

const docA: ResonaDocument = { id: "doc-a", title: "Arztbrief", date_created: "2026-06-19T08:30:00.000Z", status: "done", markdown: "# A" };
const docB: ResonaDocument = { id: "doc-b", title: "Laborwerte", date_created: "2026-06-18T14:05:00.000Z", status: "processing" };
const docC: ResonaDocument = { id: "doc-c", title: "Scan", date_created: "2026-06-17T10:00:00.000Z", status: "error", error_message: "kaputt" };

const store = vi.hoisted(() => ({
	listDocuments: vi.fn(),
	createDocument: vi.fn(),
}));
mockNuxtImport("useDataStore", () => () => store);

const navigateTo = vi.hoisted(() => vi.fn());
mockNuxtImport("navigateTo", () => navigateTo);

describe("documents list page", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		store.listDocuments.mockResolvedValue([docA, docB, docC]);
		store.createDocument.mockResolvedValue({ id: "doc-new", title: "x", status: "pending" });
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it("renders a row per document with title, de-DE date and a chip only for unfinished ones", async () => {
		const w = await mountSuspended(DocumentsPage);
		await flushPromises();
		const rows = w.findAllComponents({ name: "HListRow" });
		expect(rows.length).toBe(3);
		expect(rows[0]!.props("title")).toBe("Arztbrief");
		expect(rows[0]!.props("meta")).toBe(new Date(docA.date_created!).toLocaleString("de-DE"));
		expect(rows.map(r => r.props("status"))).toEqual(["done", "transcribing", "error"]);
		expect(w.find("[data-testid=\"doc-row-doc-a\"]").find(".h-chip").exists()).toBe(false);
		expect(w.text()).toContain("Transkribiert…");
		expect(w.text()).toContain("Fehler");
		expect(w.text()).toContain("Dokumente");
	});

	it("navigates to the detail page on row select", async () => {
		const w = await mountSuspended(DocumentsPage);
		await flushPromises();
		w.findAllComponents({ name: "HListRow" })[1]!.vm.$emit("select");
		expect(navigateTo).toHaveBeenCalledWith("/documents/doc-b");
	});

	it("has a hidden input with the document accept list", async () => {
		const w = await mountSuspended(DocumentsPage);
		const input = w.find("[data-testid=\"docs-upload-input\"]");
		expect(input.attributes("accept")).toBe("application/pdf,image/png,image/jpeg,image/tiff,.pdf,.png,.jpg,.jpeg,.tif,.tiff");
		expect(w.find("[data-testid=\"docs-upload\"]").text()).toBe("Dokument hochladen");
	});

	it("uploads a file with the extension stripped from the title and reloads", async () => {
		const w = await mountSuspended(DocumentsPage);
		await flushPromises();
		store.listDocuments.mockClear();
		const file = new File(["%PDF"], "Befund.2026.pdf", { type: "application/pdf" });
		const input = w.find("[data-testid=\"docs-upload-input\"]");
		Object.defineProperty(input.element, "files", { value: [file], configurable: true });
		await input.trigger("change");
		await flushPromises();
		expect(store.createDocument).toHaveBeenCalledWith({ title: "Befund.2026" }, file, "Befund.2026.pdf");
		expect(store.listDocuments).toHaveBeenCalled();
	});

	it("passes the search text as filter", async () => {
		const w = await mountSuspended(DocumentsPage);
		await flushPromises();
		w.findComponent({ name: "HField" }).vm.$emit("update:modelValue", "Brief");
		await flushPromises();
		expect(store.listDocuments).toHaveBeenLastCalledWith({ search: "Brief" });
	});

	it("polls every 4 s while a row is pending/processing and stops when all are terminal", async () => {
		vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
		const w = await mountSuspended(DocumentsPage);
		await flushPromises();
		expect(store.listDocuments).toHaveBeenCalledTimes(1);

		store.listDocuments.mockResolvedValue([docA, { ...docB, status: "done" }, docC]);
		vi.advanceTimersByTime(4000);
		await flushPromises();
		expect(store.listDocuments).toHaveBeenCalledTimes(2);

		vi.advanceTimersByTime(4000);
		await flushPromises();
		vi.advanceTimersByTime(4000);
		await flushPromises();
		expect(store.listDocuments).toHaveBeenCalledTimes(2);
		w.unmount();
	});
});
