import type { Recording, ResonaDocument, Transcript } from "~/types/directus";

function formatDuration(seconds?: number): string {
	if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) {
		return "";
	}
	const total = Math.round(seconds);
	const minutes = Math.floor(total / 60);
	const secs = total % 60;
	return `${minutes}:${secs.toString().padStart(2, "0")}`;
}

function slugify(title: string): string {
	const slug = title
		.toLowerCase()
		.normalize("NFKD")
		.replace(/[̀-ͯ]/g, "")
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-+|-+$/g, "");
	return slug || "aufnahme";
}

export function useExport() {
	function toText(recording: Recording, transcript: Transcript | null): string {
		return `${recording.title}\n\n${transcript?.text ?? ""}`;
	}

	function toMarkdown(recording: Recording, transcript: Transcript | null): string {
		const meta: string[] = [];
		if (recording.date_created) {
			meta.push(`- Datum: ${recording.date_created}`);
		}
		if (recording.language) {
			meta.push(`- Sprache: ${recording.language}`);
		}
		const duration = formatDuration(recording.duration_seconds);
		if (duration) {
			meta.push(`- Dauer: ${duration}`);
		}
		if (transcript?.engine) {
			meta.push(`- Engine: ${transcript.engine}`);
		}
		const metaBlock = meta.length > 0 ? `${meta.join("\n")}\n\n` : "";
		return `# ${recording.title}\n\n${metaBlock}${transcript?.text ?? ""}`;
	}

	async function copy(text: string): Promise<void> {
		await navigator.clipboard?.writeText(text);
	}

	function download(name: string, content: string, mime: string): void {
		if (typeof document === "undefined" || typeof URL?.createObjectURL !== "function") {
			return;
		}
		const blob = new Blob([content], { type: mime });
		const url = URL.createObjectURL(blob);
		const anchor = document.createElement("a");
		anchor.href = url;
		anchor.download = name;
		anchor.click();
		URL.revokeObjectURL?.(url);
	}

	function downloadText(recording: Recording, transcript: Transcript | null): void {
		download(`${slugify(recording.title)}.txt`, toText(recording, transcript), "text/plain");
	}

	function downloadMarkdown(recording: Recording, transcript: Transcript | null): void {
		download(`${slugify(recording.title)}.md`, toMarkdown(recording, transcript), "text/markdown");
	}

	function downloadDocumentMarkdown(doc: ResonaDocument): void {
		download(`${slugify(doc.title)}.md`, `# ${doc.title}\n\n${doc.markdown ?? ""}`, "text/markdown");
	}

	return {
		toText,
		toMarkdown,
		copy,
		download,
		downloadText,
		downloadMarkdown,
		downloadDocumentMarkdown,
	};
}
