import "fake-indexeddb/auto";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { Mock } from "vitest";
import { clear, createStore, values } from "idb-keyval";
import {
	createLocalStore,
	listByStatus,
	patchDocumentInternal,
	patchRecordingInternal,
	readFile,
	replaceAutoTitle,
	upsertTranscript,
} from "~/data/localStore";
import type { NewRecording } from "~/data/types";
import type { Transcript } from "~/types/directus";

const DBS = ["recordings", "transcripts", "documents", "files"] as const;

function rawStore(name: typeof DBS[number]) {
	return createStore(`resona-local-${name}`, "items");
}

function input(title: string, overrides: Partial<NewRecording> = {}): NewRecording {
	return {
		title,
		language: "de",
		profile: "default",
		source: "batch",
		status: "pending",
		duration_seconds: 12,
		...overrides,
	};
}

function audio(content = "audio-bytes"): Blob {
	return new Blob([content], { type: "audio/webm" });
}

describe("createLocalStore", () => {
	const store = createLocalStore();

	beforeEach(async () => {
		for (const name of DBS) {
			await clear(rawStore(name));
		}
	});

	it("is the standalone store", () => {
		expect(store.mode).toBe("standalone");
	});

	describe("recordings", () => {
		it("creates a recording with all NewRecording fields and reads it back", async () => {
			const rec = await store.createRecording(
				input("Visite", { source: "live", status: "done", duration_seconds: 42, language: "en", profile: "medical" }),
				null,
				"Visite.webm",
			);
			expect(rec.id).toMatch(/^[0-9a-f-]{36}$/);
			expect(rec).toMatchObject({
				title: "Visite",
				language: "en",
				profile: "medical",
				source: "live",
				status: "done",
				duration_seconds: 42,
			});
			expect(rec.audio_file).toBeUndefined();
			expect(Number.isNaN(Date.parse(rec.date_created!))).toBe(false);

			expect(await store.getRecording(rec.id)).toEqual(rec);
			expect(await store.getTranscript(rec.id)).toBeNull();
		});

		it("rejects getRecording for an unknown id", async () => {
			await expect(store.getRecording("missing")).rejects.toThrow("Aufnahme nicht gefunden");
		});

		it("lists newest first and filters by case-insensitive title and inclusive since", async () => {
			const a = await store.createRecording(input("Visite Müller"), null, "a.webm");
			const b = await store.createRecording(input("Team-Meeting"), null, "b.webm");
			const c = await store.createRecording(input("visite Schmidt"), null, "c.webm");

			expect((await store.listRecordings({})).map(r => r.id)).toEqual([c.id, b.id, a.id]);
			expect((await store.listRecordings({ search: "VISITE" })).map(r => r.id)).toEqual([c.id, a.id]);
			expect((await store.listRecordings({ search: "" })).length).toBe(3);
			// `since` equal to b's timestamp keeps b (>=) and drops the older a.
			expect((await store.listRecordings({ since: b.date_created })).map(r => r.id)).toEqual([c.id, b.id]);
			expect((await store.listRecordings({ search: "visite", since: b.date_created })).map(r => r.id)).toEqual([c.id]);
		});

		it("limits the list to the 100 newest recordings", async () => {
			const ids: string[] = [];
			for (let i = 0; i < 101; i++) {
				ids.push((await store.createRecording(input(`R${i}`), null, "r.webm")).id);
			}
			const rows = await store.listRecordings({});
			expect(rows.length).toBe(100);
			expect(rows[0]!.id).toBe(ids[100]);
			expect(rows.map(r => r.id)).not.toContain(ids[0]);
		});

		it("stores the audio and round-trips it with name and type via readFile", async () => {
			const rec = await store.createRecording(input("Notiz"), audio("abc"), "Notiz.webm");
			expect(typeof rec.audio_file).toBe("string");

			const blob = await readFile(rec.audio_file!);
			expect(blob).not.toBeNull();
			expect(blob!.type).toBe("audio/webm");
			expect((blob as File).name).toBe("Notiz.webm");
			expect(await blob!.text()).toBe("abc");
		});

		it("readFile returns null for an unknown file id", async () => {
			expect(await readFile("nope")).toBeNull();
		});

		describe("audioUrl", () => {
			const original = URL.createObjectURL;
			let createObjectURL: Mock<(obj: Blob) => string>;

			beforeEach(() => {
				createObjectURL = vi.fn((_obj: Blob) => "blob:local-audio");
				URL.createObjectURL = createObjectURL as unknown as typeof URL.createObjectURL;
			});

			afterEach(() => {
				URL.createObjectURL = original;
			});

			it("returns an object URL for the stored audio", async () => {
				const rec = await store.createRecording(input("Notiz"), audio("abcd"), "Notiz.webm");
				expect(await store.audioUrl(rec)).toBe("blob:local-audio");
				const passed = createObjectURL.mock.calls[0]![0] as Blob;
				expect(passed.size).toBe(4);
				expect(passed.type).toBe("audio/webm");
			});

			it("returns null without an audio file or when the file is gone", async () => {
				const rec = await store.createRecording(input("Ohne"), null, "Ohne.webm");
				expect(await store.audioUrl(rec)).toBeNull();
				expect(await store.audioUrl({ ...rec, audio_file: "deleted-file" })).toBeNull();
				expect(createObjectURL).not.toHaveBeenCalled();
			});
		});

		it("updateRecording patches the row and bumps date_updated", async () => {
			const rec = await store.createRecording(input("Notiz"), null, "n.webm");
			const before = (await store.getRecording(rec.id)) as { date_updated?: string };

			await store.updateRecording(rec.id, { review_status: "ready", status: "pending", error_message: null });

			const after = (await store.getRecording(rec.id)) as typeof rec & { date_updated?: string };
			expect(after).toMatchObject({ id: rec.id, title: "Notiz", review_status: "ready", status: "pending", error_message: null });
			expect(after.date_created).toBe(rec.date_created);
			expect(after.date_updated! > before.date_updated!).toBe(true);
		});

		it("updateRecording on an unknown id rejects without creating a row", async () => {
			await expect(store.updateRecording("missing", { status: "done" })).rejects.toThrow("Aufnahme nicht gefunden");
			expect(await store.listRecordings({})).toEqual([]);
		});

		it("deleteRecording cascades to its transcript and audio file only", async () => {
			const rec = await store.createRecording(input("Weg"), audio(), "Weg.webm");
			await store.createTranscript(rec.id, { text: "weg" });
			const keep = await store.createRecording(input("Bleibt"), audio(), "Bleibt.webm");
			await store.createTranscript(keep.id, { text: "bleibt" });

			await store.deleteRecording(rec.id);

			await expect(store.getRecording(rec.id)).rejects.toThrow();
			expect(await store.getTranscript(rec.id)).toBeNull();
			expect(await readFile(rec.audio_file!)).toBeNull();

			expect((await store.getTranscript(keep.id))!.text).toBe("bleibt");
			expect(await readFile(keep.audio_file!)).not.toBeNull();
			expect((await store.listRecordings({})).map(r => r.id)).toEqual([keep.id]);
		});

		it("replaceAudio stores the new file, patches the row and deletes the old file", async () => {
			const rec = await store.createRecording(input("Visite", { duration_seconds: 12 }), audio("old"), "Visite.webm");
			const other = await store.createRecording(input("Bleibt"), audio("keep"), "Bleibt.webm");

			const res = await store.replaceAudio(rec.id, new Blob(["merged"], { type: "audio/wav" }), "Visite.wav", 18.5);

			expect(res.audio_file).not.toBe(rec.audio_file);
			expect(res.duration_seconds).toBe(18.5);
			expect(await store.getRecording(rec.id)).toMatchObject({ audio_file: res.audio_file, duration_seconds: 18.5 });
			const stored = await readFile(res.audio_file);
			expect(stored!.type).toBe("audio/wav");
			expect((stored as File).name).toBe("Visite.wav");
			expect(await stored!.text()).toBe("merged");
			expect(await readFile(rec.audio_file!)).toBeNull();
			expect(await readFile(other.audio_file!)).not.toBeNull();
		});

		it("replaceAudio gives a recording without audio its first file", async () => {
			const rec = await store.createRecording(input("Live", { source: "live", status: "done" }), null, "Live.webm");
			const res = await store.replaceAudio(rec.id, new Blob(["new"], { type: "audio/wav" }), "Live.wav", 6);
			expect(await store.getRecording(rec.id)).toMatchObject({ audio_file: res.audio_file, duration_seconds: 6 });
		});

		it("replaceAudio on an unknown id rejects and leaves no file behind", async () => {
			await expect(store.replaceAudio("missing", audio(), "x.wav", 1)).rejects.toThrow("Aufnahme nicht gefunden");
			expect(await values(rawStore("files"))).toEqual([]);
		});
	});

	describe("transcripts", () => {
		it("creates, reads and updates a transcript keeping recording and engine", async () => {
			const rec = await store.createRecording(input("Live"), null, "Live.webm");
			const t = await store.createTranscript(rec.id, { text: "hallo", engine: "faster-whisper" });
			expect(t).toMatchObject({ recording: rec.id, text: "hallo", engine: "faster-whisper" });

			await store.updateTranscript(t.id, { text: "hallo welt" });

			const read = await store.getTranscript(rec.id);
			expect(read).toMatchObject({ id: t.id, recording: rec.id, text: "hallo welt", engine: "faster-whisper" });
			expect(read!.date_updated! > t.date_updated!).toBe(true);
		});

		it("updateTranscript on an unknown id rejects", async () => {
			await expect(store.updateTranscript("missing", { text: "x" })).rejects.toThrow("Transkript nicht gefunden");
			expect(await values(rawStore("transcripts"))).toEqual([]);
		});
	});

	describe("documents", () => {
		it("creates a pending document with its file, lists, updates and deletes it", async () => {
			const pdf = new Blob(["%PDF-1.7"], { type: "application/pdf" });
			const doc = await store.createDocument({ title: "Befund" }, pdf, "Befund.pdf");
			expect(doc).toMatchObject({ title: "Befund", status: "pending" });
			expect(typeof doc.file).toBe("string");
			const file = await readFile(doc.file!);
			expect((file as File).name).toBe("Befund.pdf");
			expect(file!.type).toBe("application/pdf");

			const other = await store.createDocument({ title: "Rechnung" }, pdf, "Rechnung.pdf");
			expect((await store.listDocuments({})).map(d => d.id)).toEqual([other.id, doc.id]);
			expect((await store.listDocuments({ search: "befu" })).map(d => d.id)).toEqual([doc.id]);

			await store.updateDocument(doc.id, { status: "error", error_message: "kaputt" });
			expect(await store.getDocument(doc.id)).toMatchObject({ status: "error", error_message: "kaputt", file: doc.file });

			await store.deleteDocument(doc.id);
			await expect(store.getDocument(doc.id)).rejects.toThrow("Dokument nicht gefunden");
			expect(await readFile(doc.file!)).toBeNull();
			expect(await readFile(other.file!)).not.toBeNull();
			expect((await store.listDocuments({})).map(d => d.id)).toEqual([other.id]);
		});

		it("updateDocument on an unknown id rejects", async () => {
			await expect(store.updateDocument("missing", { status: "done" })).rejects.toThrow("Dokument nicht gefunden");
			expect(await store.listDocuments({})).toEqual([]);
		});
	});

	describe("processor helpers", () => {
		it("listByStatus returns matching rows oldest first", async () => {
			const first = await store.createRecording(input("eins"), null, "1.webm");
			await store.createRecording(input("zwei", { status: "done" }), null, "2.webm");
			const third = await store.createRecording(input("drei"), null, "3.webm");

			expect((await listByStatus("recordings", "pending")).map(r => r.id)).toEqual([first.id, third.id]);
			expect(await listByStatus("recordings", "transcribing")).toEqual([]);

			const pdf = new Blob(["%PDF"], { type: "application/pdf" });
			const d1 = await store.createDocument({ title: "a" }, pdf, "a.pdf");
			const d2 = await store.createDocument({ title: "b" }, pdf, "b.pdf");
			await patchDocumentInternal(d1.id, { status: "processing", hub_job_id: 7 });
			await patchDocumentInternal(d2.id, { status: "processing", hub_job_id: 8 });
			expect((await listByStatus("documents", "processing")).map(d => d.id)).toEqual([d1.id, d2.id]);
			expect(await listByStatus("documents", "pending")).toEqual([]);
		});

		it("patchRecordingInternal sets processor fields and keeps the id", async () => {
			const rec = await store.createRecording(input("Job"), null, "j.webm");
			await patchRecordingInternal(rec.id, { id: "hijack", status: "transcribing", hub_job_id: 42, error_message: null });
			const row = await store.getRecording(rec.id);
			expect(row).toMatchObject({ id: rec.id, status: "transcribing", hub_job_id: 42, title: "Job" });
		});

		it("replaceAutoTitle writes only while title_auto is set", async () => {
			const auto = await store.createRecording({ ...input("Aufnahme 10.11.2026 09:05"), title_auto: true }, null, "a.webm");
			const renamed = await store.createRecording({ ...input("Mein Titel"), title_auto: false }, null, "m.webm");
			await replaceAutoTitle(auto.id, "Befund Knie");
			await replaceAutoTitle(renamed.id, "Befund Knie");
			await expect(replaceAutoTitle("gone", "x")).resolves.toBeUndefined();
			expect(await store.getRecording(auto.id)).toMatchObject({ title: "Befund Knie", title_auto: false });
			expect(await store.getRecording(renamed.id)).toMatchObject({ title: "Mein Titel", title_auto: false });
		});

		it("replaceAutoTitle never overwrites a recording renamed through the store", async () => {
			const rec = await store.createRecording({ ...input("Aufnahme 10.11.2026 09:05"), title_auto: true }, null, "a.webm");
			await store.updateRecording(rec.id, { title: "Visite Herr M.", title_auto: false });
			await replaceAutoTitle(rec.id, "Wundkontrolle");
			expect(await store.getRecording(rec.id)).toMatchObject({ title: "Visite Herr M.", title_auto: false });
		});

		it("internal patches of a deleted row are no-ops", async () => {
			await expect(patchRecordingInternal("gone", { status: "done" })).resolves.toBeUndefined();
			await expect(patchDocumentInternal("gone", { status: "done" })).resolves.toBeUndefined();
			expect(await values(rawStore("recordings"))).toEqual([]);
			expect(await values(rawStore("documents"))).toEqual([]);
		});

		it("upsertTranscript creates once and then replaces in place", async () => {
			const rec = await store.createRecording(input("Batch"), audio(), "b.webm");
			await upsertTranscript(rec.id, { text: "erst", segments: [{ start: 0, end: 1, text: "erst" }], engine: "whisper" });
			const created = await store.getTranscript(rec.id);
			expect(created).toMatchObject({ recording: rec.id, text: "erst", engine: "whisper" });
			expect(created!.segments).toEqual([{ start: 0, end: 1, text: "erst" }]);

			await upsertTranscript(rec.id, { text: "neu", structured: { title: "x" }, engine: null });

			const all = await values<Transcript>(rawStore("transcripts"));
			expect(all.length).toBe(1);
			expect(all[0]).toMatchObject({ id: created!.id, recording: rec.id, text: "neu", structured: { title: "x" }, segments: null });
			expect(all[0]!.date_created).toBe(created!.date_created);
		});

		it("upsertTranscript replaces a transcript created through the store", async () => {
			const rec = await store.createRecording(input("Live", { source: "live", status: "done" }), null, "l.webm");
			const t = await store.createTranscript(rec.id, { text: "live" });
			await upsertTranscript(rec.id, { text: "korrigiert" });
			const all = await values<Transcript>(rawStore("transcripts"));
			expect(all.map(x => [x.id, x.text])).toEqual([[t.id, "korrigiert"]]);
		});

		it("upsertTranscript for a deleted recording leaves no orphan", async () => {
			await upsertTranscript("gone", { text: "x" });
			expect(await values(rawStore("transcripts"))).toEqual([]);
		});
	});
});
