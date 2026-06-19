import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useExport } from "~/composables/useExport";
import type { Recording, Transcript } from "~/types/directus";

function baseRecording(over: Partial<Recording> = {}): Recording {
	return {
		id: "rec-1",
		title: "Visite Station 3",
		date_created: "2026-06-19T08:30:00.000Z",
		duration_seconds: 42,
		language: "de",
		profile: "default",
		status: "done",
		source: "batch",
		...over,
	};
}

function baseTranscript(over: Partial<Transcript> = {}): Transcript {
	return {
		id: "t-1",
		recording: "rec-1",
		text: "Hallo Welt, dies ist ein Befund.",
		engine: "faster-whisper",
		...over,
	};
}

describe("useExport", () => {
	describe("toText", () => {
		it("returns the title followed by the transcript text", () => {
			const { toText } = useExport();
			const out = toText(baseRecording(), baseTranscript());
			expect(out).toContain("Visite Station 3");
			expect(out).toContain("Hallo Welt, dies ist ein Befund.");
			expect(out).toBe("Visite Station 3\n\nHallo Welt, dies ist ein Befund.");
		});

		it("tolerates a null transcript", () => {
			const { toText } = useExport();
			const out = toText(baseRecording(), null);
			expect(out).toBe("Visite Station 3\n\n");
		});
	});

	describe("toMarkdown", () => {
		it("returns an MD heading, metadata and the body", () => {
			const { toMarkdown } = useExport();
			const out = toMarkdown(baseRecording(), baseTranscript());
			expect(out).toContain("# Visite Station 3");
			// metadata tokens
			expect(out).toContain("de");
			expect(out).toContain("faster-whisper");
			// body
			expect(out).toContain("Hallo Welt, dies ist ein Befund.");
		});

		it("omits engine metadata when absent", () => {
			const { toMarkdown } = useExport();
			const out = toMarkdown(baseRecording(), baseTranscript({ engine: undefined }));
			expect(out).not.toContain("faster-whisper");
			expect(out).toContain("# Visite Station 3");
		});
	});

	describe("copy", () => {
		let original: typeof navigator.clipboard;

		beforeEach(() => {
			original = navigator.clipboard;
		});

		afterEach(() => {
			Object.defineProperty(navigator, "clipboard", { value: original, configurable: true });
		});

		it("calls navigator.clipboard.writeText with the text", async () => {
			const writeText = vi.fn(async () => {});
			Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
			const { copy } = useExport();
			await copy("hello clipboard");
			expect(writeText).toHaveBeenCalledWith("hello clipboard");
		});
	});

	describe("download", () => {
		let createObjectURL: ReturnType<typeof vi.fn>;
		let revokeObjectURL: ReturnType<typeof vi.fn>;
		let click: ReturnType<typeof vi.fn>;
		let origCreate: typeof URL.createObjectURL;
		let origRevoke: typeof URL.revokeObjectURL;
		let origClick: typeof HTMLAnchorElement.prototype.click;

		beforeEach(() => {
			origCreate = URL.createObjectURL;
			origRevoke = URL.revokeObjectURL;
			origClick = HTMLAnchorElement.prototype.click;
			createObjectURL = vi.fn(() => "blob:x");
			revokeObjectURL = vi.fn();
			click = vi.fn();
			URL.createObjectURL = createObjectURL;
			URL.revokeObjectURL = revokeObjectURL;
			HTMLAnchorElement.prototype.click = click;
		});

		afterEach(() => {
			URL.createObjectURL = origCreate;
			URL.revokeObjectURL = origRevoke;
			HTMLAnchorElement.prototype.click = origClick;
		});

		it("creates a blob URL, clicks an anchor and revokes the URL", () => {
			const { download } = useExport();
			download("note.txt", "some content", "text/plain");
			expect(createObjectURL).toHaveBeenCalledTimes(1);
			expect(createObjectURL.mock.calls[0]![0]).toBeInstanceOf(Blob);
			expect(click).toHaveBeenCalledTimes(1);
			expect(revokeObjectURL).toHaveBeenCalledWith("blob:x");
		});
	});

	describe("download wrappers", () => {
		let origCreate: typeof URL.createObjectURL;
		let origRevoke: typeof URL.revokeObjectURL;
		let origCreateElement: typeof document.createElement;
		let anchors: HTMLAnchorElement[];

		beforeEach(() => {
			origCreate = URL.createObjectURL;
			origRevoke = URL.revokeObjectURL;
			origCreateElement = document.createElement.bind(document);
			URL.createObjectURL = vi.fn(() => "blob:x");
			URL.revokeObjectURL = vi.fn();
			anchors = [];
			vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
				const el = origCreateElement(tag) as HTMLElement;
				if (tag === "a") {
					vi.spyOn(el as HTMLAnchorElement, "click").mockImplementation(() => {});
					anchors.push(el as HTMLAnchorElement);
				}
				return el;
			});
		});

		afterEach(() => {
			URL.createObjectURL = origCreate;
			URL.revokeObjectURL = origRevoke;
			vi.restoreAllMocks();
		});

		it("downloadText derives a .txt filename from the title", () => {
			const { downloadText } = useExport();
			downloadText(baseRecording(), baseTranscript());
			expect(anchors[0]?.download).toMatch(/\.txt$/);
			expect(anchors[0]?.download).toContain("visite-station-3");
		});

		it("downloadMarkdown derives a .md filename from the title", () => {
			const { downloadMarkdown } = useExport();
			downloadMarkdown(baseRecording(), baseTranscript());
			expect(anchors[0]?.download).toMatch(/\.md$/);
		});
	});
});
