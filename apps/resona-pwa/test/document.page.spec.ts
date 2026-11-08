import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { flushPromises } from "@vue/test-utils";
import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import DocumentPage from "~/pages/documents/[id].vue";
import type { ResonaDocument } from "~/types/directus";

const doneDoc: ResonaDocument = { id: "doc-1", title: "Arztbrief Müller", status: "done", markdown: "## Befund\n\nAlles **gut**." };
const errorDoc: ResonaDocument = { id: "doc-1", title: "Arztbrief Müller", status: "error", error_message: "OCR fehlgeschlagen", markdown: null };

const store = vi.hoisted(() => ({
	getDocument: vi.fn(),
	updateDocument: vi.fn(),
	deleteDocument: vi.fn(),
}));
mockNuxtImport("useDataStore", () => () => store);

const navigateTo = vi.hoisted(() => vi.fn());
mockNuxtImport("navigateTo", () => navigateTo);
const nav = vi.hoisted(() => ({ back: vi.fn(async () => {}), leaveTo: vi.fn(async () => {}) }));
mockNuxtImport("useBackNav", () => () => nav);
mockNuxtImport("useRoute", () => () => ({ params: { id: "doc-1" } }));

const exportFns = vi.hoisted(() => ({ copy: vi.fn(async () => {}), downloadDocumentMarkdown: vi.fn() }));
mockNuxtImport("useExport", () => () => exportFns);

describe("document detail page", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		store.getDocument.mockResolvedValue(doneDoc);
		store.updateDocument.mockResolvedValue(undefined);
		store.deleteDocument.mockResolvedValue(undefined);
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it("renders the full title, no chip for a finished document and markdown in a pre", async () => {
		const w = await mountSuspended(DocumentPage);
		await flushPromises();
		expect(store.getDocument).toHaveBeenCalledWith("doc-1");
		expect(w.find("[data-testid=\"doc-title\"]").text()).toBe("Arztbrief Müller");
		expect(w.find("[data-testid=\"doc-bar-title\"]").text()).toBe("Arztbrief Müller");
		expect(w.findComponent({ name: "StatusChip" }).exists()).toBe(false);
		const pre = w.find("pre.doc__markdown");
		expect(pre.exists()).toBe(true);
		expect(pre.text()).toBe("## Befund\n\nAlles **gut**.");
		expect(w.find("[data-testid=\"doc-error-banner\"]").exists()).toBe(false);
	});

	it("maps processing to the transcribing chip and polls until done", async () => {
		vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
		store.getDocument.mockResolvedValue({ ...doneDoc, status: "processing", markdown: null });
		const w = await mountSuspended(DocumentPage);
		await flushPromises();
		expect(w.findComponent({ name: "StatusChip" }).props("status")).toBe("transcribing");

		store.getDocument.mockResolvedValue(doneDoc);
		vi.advanceTimersByTime(4000);
		await flushPromises();
		expect(store.getDocument).toHaveBeenCalledTimes(2);
		expect(w.find("pre.doc__markdown").exists()).toBe(true);

		vi.advanceTimersByTime(8000);
		await flushPromises();
		expect(store.getDocument).toHaveBeenCalledTimes(2);
		w.unmount();
	});

	it("shows the error banner and retries via updateDocument", async () => {
		store.getDocument.mockResolvedValue(errorDoc);
		const w = await mountSuspended(DocumentPage);
		await flushPromises();
		const banner = w.find("[data-testid=\"doc-error-banner\"]");
		expect(banner.text()).toContain("OCR fehlgeschlagen");
		const retry = w.find("[data-testid=\"doc-retry\"]");
		expect(retry.text()).toBe("Erneut verarbeiten");
		store.getDocument.mockResolvedValue({ ...errorDoc, status: "pending", error_message: null });
		await retry.trigger("click");
		await flushPromises();
		expect(store.updateDocument).toHaveBeenCalledWith("doc-1", { status: "pending", error_message: null });
		expect(store.getDocument).toHaveBeenCalledTimes(2);
		expect(w.find("[data-testid=\"doc-error-banner\"]").exists()).toBe(false);
		w.unmount();
	});

	it("the back arrow goes through the shared back navigation to the document list", async () => {
		const w = await mountSuspended(DocumentPage);
		await flushPromises();
		await w.find("[data-testid=\"doc-back\"]").trigger("click");
		expect(nav.back).toHaveBeenCalledWith("/documents");
		expect(navigateTo).not.toHaveBeenCalled();
	});

	it("deletes only after confirmation and returns to the list", async () => {
		const w = await mountSuspended(DocumentPage);
		await flushPromises();
		await w.find("[data-testid=\"doc-delete\"]").trigger("click");
		expect(store.deleteDocument).not.toHaveBeenCalled();
		await w.find("[data-testid=\"doc-cancel-delete\"]").trigger("click");
		expect(w.find("[data-testid=\"doc-confirm-bar\"]").exists()).toBe(false);
		expect(store.deleteDocument).not.toHaveBeenCalled();

		await w.find("[data-testid=\"doc-delete\"]").trigger("click");
		await w.find("[data-testid=\"doc-confirm-delete\"]").trigger("click");
		await flushPromises();
		expect(store.deleteDocument).toHaveBeenCalledWith("doc-1");
		expect(nav.leaveTo).toHaveBeenCalledWith("/documents");
	});

	it("copies the markdown", async () => {
		const w = await mountSuspended(DocumentPage);
		await flushPromises();
		await w.find("[data-testid=\"doc-copy\"]").trigger("click");
		expect(exportFns.copy).toHaveBeenCalledWith("## Befund\n\nAlles **gut**.");
	});

	it("downloads the document as markdown", async () => {
		const w = await mountSuspended(DocumentPage);
		await flushPromises();
		const btn = w.find("[data-testid=\"doc-download\"]");
		expect(btn.attributes("aria-label")).toBe("Als .md laden");
		await btn.trigger("click");
		expect(exportFns.downloadDocumentMarkdown).toHaveBeenCalledWith(doneDoc);
	});
});
