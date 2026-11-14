import { computed, onScopeDispose, ref, watch, type ComputedRef, type Ref } from "vue";
import { discardUnfinished } from "~/data/unfinishedRecordings";
import { mergeToWav } from "~/utils/audioMerge";
import { audioExtension } from "~/utils/audioFile";
import type { Recording, RecordingPart } from "~/types/directus";

/**
 * A stopped part, shown while it is not finished on this page. `stored`: its row
 * (`recording_parts`, or `resona-local-parts` standalone) is being created, exists,
 * or could not be created (then it lives only in memory and waits for `retry`).
 * `status`: the processor transcribes it, it failed (retry/discard), or its text
 * landed. `audio`: it is being appended to the recording, is saved there, or that
 * failed (`retryAudio`). A part leaves the list once its text landed and its audio
 * is saved.
 */
export interface RedictationPart {
	id: string;
	stored: "saving" | "saved" | "failed";
	status: "transcribing" | "error" | "done";
	/** German message while `status` is `error`. */
	error: string | null;
	audio: "appending" | "saved" | "failed";
	/** German message while `audio` is `failed`. */
	audioError: string | null;
}

/** What happened to a part's audio after it was appended to the recording. */
export type RedictationAudio
	= | { partId: string; ok: true; audio_file: string; duration_seconds: number }
		| { partId: string; ok: false; error: string };

/** Where a part's text goes: an offset in the saved transcript text and that text's SHA-256. */
export interface RedictationAnchor {
	anchor_offset: number | null;
	anchor_text_sha: string | null;
}

export interface RedictationHooks {
	/** The recording as it is now (audio file and duration change with every merge). */
	recording(): Recording | null;
	/** Where the chip of `partId` stands, carried over onto the saved text. */
	anchor(partId: string): Promise<RedictationAnchor>;
	/** A stored part this page did not record (opened again): show its chip at its anchor. */
	onRestore(part: RecordingPart): void | Promise<void>;
	/**
	 * The part's text arrived (trimmed; empty when nothing was recognised, or when
	 * another tab already landed or discarded it). Resolves true once the text is in
	 * the saved transcript; false leaves the part unapplied until `saved()`.
	 */
	onText(partId: string, text: string): boolean | Promise<boolean>;
	/** The part's transcription failed; it stays for `retry` or `discard`. */
	onError(partId: string, message: string): void;
	/** A part's audio was appended to the recording (in stop order), or that failed. */
	onAudio(result: RedictationAudio): void;
}

/** The open part's meter, pause and interruption controls, for the record bar. */
export interface RedictationMeter {
	elapsed: Ref<number>;
	levels: Ref<number[]>;
	paused: Ref<boolean>;
	/** German text while the part is interrupted (microphone lost, app in background), else null. */
	interruption: ComputedRef<string | null>;
	pause(): void;
	resume(): void;
	/** Records on after an interruption; the new audio joins the same part. */
	continueRecording(): Promise<boolean>;
}

export interface Redictation {
	/** Own recorder (separate from push-to-talk): meter and pause of the open part. */
	recorder: RedictationMeter;
	/** The microphone is open (or opening) for a part. */
	recording: Ref<boolean>;
	/** Why the last part could not start or was empty (German), else null. */
	startError: Ref<string | null>;
	/** Parts not finished on this page, in recording order. */
	parts: Ref<RedictationPart[]>;
	/** Parts the processor is transcribing. */
	transcribing: ComputedRef<number>;
	/** Audio of stopped parts still being appended to the recording. */
	merging: ComputedRef<boolean>;
	/** Something would be lost by leaving: mic open, a part being stored or audio being appended. */
	busy: ComputedRef<boolean>;
	/** Parts that could not be stored and live only in memory: leaving loses them. */
	failed: ComputedRef<number>;
	/** Opens the microphone for part `partId` (a uuid, also the row id); false when it could not start. */
	start(partId: string): Promise<boolean>;
	/** Closes the microphone and drops what it captured. */
	cancel(): Promise<void>;
	/** Stops the part: it is stored, its audio appended, the processor transcribes it. False when it was empty. */
	finish(): Promise<boolean>;
	/** Stores a part again after that failed, or has a failed transcription run again. */
	retry(partId: string): void;
	/** Appends a part's audio again after the append failed. */
	retryAudio(partId: string): void;
	/** Gives a failed part up; a stored one is marked `discarded` (its audio stays in the recording). */
	discard(partId: string): void;
	/** The transcript was saved: parts landed earlier without a successful save are now applied. */
	saved(): void;
	/** Stores new anchors (after a save moved the text); unchanged ones are not written. */
	reanchor(anchors: Map<string, RedictationAnchor>): void;
}

const POLL_MS = 2000;
const CLOSED = "Die Aufnahme ist nicht mehr geöffnet.";
const TRANSCRIBE_FAILED = "Transkription fehlgeschlagen.";

function errorText(err: unknown): string {
	return err instanceof Error ? err.message : String(err);
}

/** Upload name for a recorded part, extension from its MIME type. */
function partFilename(part: Blob, recording: Recording): string {
	return `${recording.title || recording.id}-nachdiktat.${audioExtension(part.type)}`;
}

function onBeforeUnload(event: Event): void {
	event.preventDefault();
}

/** Rows the processor still works on. */
function inFlight(row: RecordingPart): boolean {
	return row.status === "pending" || row.status === "transcribing";
}

/**
 * Re-dictation: records parts with its own recorder, one at a time. A stopped
 * part is stored right away as a part row with its audio; the project worker
 * (Directus) or the in-browser processor (standalone) runs its hub job, so the
 * part keeps transcribing after the page closed. This page polls the recording's
 * parts while one is in flight and hands each finished text to `onText`, then
 * marks the part `applied`. Its audio is appended to the recording's file (one
 * 16 kHz mono WAV, parts strictly in stop order) and the part's time range stored.
 * Opened again, the recording's unfinished parts come back through `onRestore`,
 * and parts whose audio never reached the recording are appended in order.
 * Only parts that could not be stored need the page open: `beforeunload` is
 * guarded while `busy` or a part `failed`.
 */
export function useRedictation(hooks: RedictationHooks): Redictation {
	// Every part also goes to IndexedDB while recorded, so a killed page offers it again.
	const recorder = useRecorder({
		persist: () => ({ kind: "redictation", label: `Nachdiktat ${hooks.recording()?.title ?? ""}`.trim() }),
	});
	const store = useDataStore();

	const recording = ref(false);
	const startError: Ref<string | null> = ref(null);
	const parts: Ref<RedictationPart[]> = ref([]);
	const pendingMerges = ref(0);
	const creating = ref(0);
	const transcribing = computed(() => parts.value.filter(p => p.status === "transcribing").length);
	const merging = computed(() => pendingMerges.value > 0);
	const busy = computed(() => recording.value || creating.value > 0 || merging.value);
	const failed = computed(() => parts.value.filter(p => p.stored === "failed").length);

	// The part the microphone is open for; bumped `micAttempt` makes a pending start stale.
	let micPart: string | null = null;
	let micAttempt = 0;
	// Audio blobs of parts recorded here, kept until the part is stored and its audio saved.
	const blobs = new Map<string, Blob>();
	// Row fields of parts recorded here, for (re)creating the row.
	const inputs = new Map<string, { seq: number; anchor: RedictationAnchor }>();
	// Time range of a part whose audio was appended before its row existed.
	const ranges = new Map<string, { offset_seconds: number; duration_seconds: number }>();
	// Creation of each part's row; the audio append waits for it to write the time range.
	const creations = new Map<string, Promise<void>>();
	// Persisted recorder session per stopped part, dropped once the part is stored.
	const sessions = new Map<string, string>();
	// Stored anchors, so `reanchor` writes only what changed.
	const anchors = new Map<string, RedictationAnchor>();
	// Landed parts whose transcript save failed: applied with the next save.
	const unapplied = new Set<string>();
	// Parts whose text was handed to the page (or is being handed), never twice.
	const delivered = new Set<string>();
	const wakers = new Set<() => void>();
	let disposed = false;
	let polling = false;
	let loadedFor: string | null = null;
	let loading: Promise<void> = Promise.resolve();
	let maxSeq = 0;
	// Appends run one after another, so parts join the audio in stop order.
	let mergeChain: Promise<void> = Promise.resolve();

	if (typeof window !== "undefined") {
		watch(() => busy.value || failed.value > 0, (on) => {
			if (on) window.addEventListener("beforeunload", onBeforeUnload);
			else window.removeEventListener("beforeunload", onBeforeUnload);
		});
	}

	function find(id: string): RedictationPart | undefined {
		return parts.value.find(p => p.id === id);
	}

	function setPart(id: string, patch: Partial<RedictationPart>): void {
		parts.value = parts.value.map(p => (p.id === id ? { ...p, ...patch } : p));
	}

	/** Drops a part whose text landed and whose audio is saved: nothing of it is left to lose. */
	function settle(id: string): void {
		const part = find(id);
		if (part?.status === "done" && part.audio === "saved" && part.stored === "saved") forget(id);
	}

	function forget(id: string): void {
		parts.value = parts.value.filter(p => p.id !== id);
		blobs.delete(id);
		inputs.delete(id);
		ranges.delete(id);
	}

	function dropSession(partId: string): void {
		const session = sessions.get(partId);
		sessions.delete(partId);
		if (session) void discardUnfinished(session);
	}

	// Executor form instead of Promise.withResolvers: iOS Safari < 17.4.
	function sleep(ms: number): Promise<void> {
		return new Promise((resolve) => {
			const wake = () => {
				clearTimeout(timer);
				wakers.delete(wake);
				resolve();
			};
			const timer = setTimeout(wake, ms);
			wakers.add(wake);
		});
	}

	async function markApplied(id: string): Promise<void> {
		try {
			await store.updatePart(id, { status: "applied" });
			unapplied.delete(id);
		}
		catch {
			// Stays `done`: the next save tries again, a reopen lands it again.
			unapplied.add(id);
		}
	}

	/** Hands the part's text to the page; `applied` once it is in the saved transcript. */
	async function deliver(id: string, text: string, apply: boolean): Promise<void> {
		if (delivered.has(id)) return;
		delivered.add(id);
		setPart(id, { status: "done", error: null });
		settle(id);
		let landed: boolean;
		try {
			landed = await hooks.onText(id, text);
		}
		catch {
			landed = false;
		}
		if (!apply || disposed) return;
		if (landed) await markApplied(id);
		else unapplied.add(id);
	}

	/** Brings the in-memory part in line with its stored row. */
	function sync(row: RecordingPart): void {
		const part = find(row.id);
		if (!part || part.stored !== "saved") return;
		if (inFlight(row)) {
			if (part.status === "error") setPart(row.id, { status: "transcribing", error: null });
		}
		else if (row.status === "done") {
			void deliver(row.id, (row.text ?? "").trim(), true);
		}
		else if (row.status === "error") {
			if (part.status === "error") return;
			const message = row.error_message || TRANSCRIBE_FAILED;
			setPart(row.id, { status: "error", error: message });
			hooks.onError(row.id, message);
		}
		else {
			// Landed or given up elsewhere (another tab): only the chip goes.
			void deliver(row.id, "", false);
		}
	}

	async function pollLoop(): Promise<void> {
		if (polling) return;
		polling = true;
		try {
			while (!disposed && parts.value.some(p => p.stored === "saved" && p.status === "transcribing")) {
				await sleep(POLL_MS);
				const rec = hooks.recording();
				if (disposed || !rec) break;
				let rows: RecordingPart[];
				try {
					rows = await store.listParts(rec.id);
				}
				catch {
					continue;
				}
				if (disposed) break;
				const seen = new Set(rows.map(r => r.id));
				for (const row of rows) sync(row);
				for (const part of parts.value) {
					// Deleted meanwhile (recording deleted elsewhere): only the chip goes.
					if (part.stored === "saved" && part.status === "transcribing" && !seen.has(part.id)) void deliver(part.id, "", false);
				}
			}
		}
		finally {
			polling = false;
		}
	}

	async function loadStoredAudio(rec: Recording): Promise<Blob | null> {
		const url = await store.audioUrl(rec);
		if (!url) return null;
		try {
			const resp = await fetch(url);
			if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
			return await resp.blob();
		}
		finally {
			// The local store hands out object URLs; Directus asset URLs need no cleanup.
			if (url.startsWith("blob:")) URL.revokeObjectURL(url);
		}
	}

	/** Appends `blob` to the recording's stored audio and stores the result. */
	async function appendAudio(partId: string, blob: Blob): Promise<{ result: RedictationAudio; offset: number }> {
		const rec = hooks.recording();
		if (!rec) return { result: { partId, ok: false, error: CLOSED }, offset: 0 };
		let stored: Blob | null;
		try {
			stored = await loadStoredAudio(rec);
		}
		catch {
			return { result: { partId, ok: false, error: "Bisheriges Audio konnte nicht geladen werden." }, offset: 0 };
		}
		const offset = stored ? rec.duration_seconds ?? 0 : 0;
		let merged;
		try {
			merged = await mergeToWav(stored ? [stored, blob] : [blob]);
		}
		catch (err) {
			return { result: { partId, ok: false, error: errorText(err) }, offset };
		}
		try {
			const saved = await store.replaceAudio(rec.id, merged.blob, `${rec.title || rec.id}.wav`, merged.seconds);
			return { result: { partId, ok: true, ...saved }, offset };
		}
		catch (err) {
			return { result: { partId, ok: false, error: `Speichern fehlgeschlagen: ${errorText(err)}` }, offset };
		}
	}

	/** Writes where the part's audio sits in the recording; kept for later when its row is missing. */
	async function storeRange(partId: string, range: { offset_seconds: number; duration_seconds: number }): Promise<void> {
		await creations.get(partId);
		if (find(partId)?.stored === "failed") {
			ranges.set(partId, range);
			return;
		}
		try {
			await store.updatePart(partId, range);
		}
		catch {
			// Then a reopen appends this part once more; nothing is lost.
		}
	}

	/** Appends a part's audio in the chain; `load` supplies it (memory, or the part's stored file). */
	function queueAudio(partId: string, load: () => Promise<Blob | null>): void {
		pendingMerges.value += 1;
		setPart(partId, { audio: "appending", audioError: null });
		mergeChain = mergeChain
			.then(async () => {
				const blob = await load();
				if (!blob) {
					setPart(partId, { audio: "saved", audioError: null });
					settle(partId);
					return;
				}
				const { result, offset } = await appendAudio(partId, blob);
				if (result.ok) {
					setPart(partId, { audio: "saved", audioError: null });
					await storeRange(partId, { offset_seconds: offset, duration_seconds: Math.max(0, result.duration_seconds - offset) });
					settle(partId);
				}
				else {
					setPart(partId, { audio: "failed", audioError: result.error });
				}
				// The page updates the recording's audio file here, before the next append reads it.
				hooks.onAudio(result);
			})
			// Nothing that throws here may break the chain: later parts still get appended.
			.catch((err: unknown) => {
				const message = errorText(err);
				if (find(partId)?.audio === "appending") {
					setPart(partId, { audio: "failed", audioError: message });
				}
				try {
					hooks.onAudio({ partId, ok: false, error: message });
				}
				catch {
					// The part's audio state above still reports it.
				}
			})
			.finally(() => {
				pendingMerges.value -= 1;
			});
	}

	/** Creates the part's row with its audio; on success the recorder's copy goes. */
	function createRow(id: string): Promise<void> {
		const blob = blobs.get(id);
		const input = inputs.get(id);
		const rec = hooks.recording();
		creating.value += 1;
		setPart(id, { stored: "saving", status: "transcribing", error: null });
		const done = (async () => {
			try {
				if (!blob || !input || !rec) throw new Error(CLOSED);
				await store.createPart({
					id,
					recording: rec.id,
					seq: input.seq,
					language: rec.language,
					profile: rec.profile,
					...input.anchor,
				}, blob, partFilename(blob, rec));
				anchors.set(id, input.anchor);
				setPart(id, { stored: "saved" });
				dropSession(id);
				const range = ranges.get(id);
				ranges.delete(id);
				if (range) await store.updatePart(id, range).catch(() => {});
				settle(id);
				void pollLoop();
			}
			catch (err) {
				const message = `Nachdiktat nicht gespeichert: ${errorText(err)}`;
				setPart(id, { stored: "failed", status: "error", error: message });
				hooks.onError(id, message);
			}
			finally {
				creating.value -= 1;
			}
		})();
		creations.set(id, done);
		return done;
	}

	/** Loads the recording's stored parts once: unfinished ones come back, unappended audio is appended. */
	async function load(rec: Recording): Promise<void> {
		let rows: RecordingPart[];
		try {
			rows = await store.listParts(rec.id);
		}
		catch {
			return;
		}
		if (disposed) return;
		for (const row of rows) {
			maxSeq = Math.max(maxSeq, row.seq);
			anchors.set(row.id, { anchor_offset: row.anchor_offset ?? null, anchor_text_sha: row.anchor_text_sha ?? null });
			if (row.status === "applied" || row.status === "discarded" || find(row.id)) continue;
			parts.value = [...parts.value, {
				id: row.id,
				stored: "saved",
				status: row.status === "error" ? "error" : inFlight(row) ? "transcribing" : "done",
				error: row.status === "error" ? row.error_message || TRANSCRIBE_FAILED : null,
				audio: "saved",
				audioError: null,
			}];
			try {
				await hooks.onRestore(row);
			}
			catch {
				// Without its chip the text still lands, at the end.
			}
			if (disposed) return;
			if (row.status === "done") void deliver(row.id, (row.text ?? "").trim(), true);
			else if (row.status === "error") hooks.onError(row.id, row.error_message || TRANSCRIBE_FAILED);
		}
		for (const row of rows) {
			if (!row.audio || row.offset_seconds != null) continue;
			if (!find(row.id)) {
				parts.value = [...parts.value, { id: row.id, stored: "saved", status: "done", error: null, audio: "appending", audioError: null }];
			}
			queueAudio(row.id, () => store.partAudio(row));
		}
		void pollLoop();
	}

	watch(() => hooks.recording()?.id ?? null, (id) => {
		const rec = hooks.recording();
		if (!id || !rec || loadedFor === id) return;
		loadedFor = id;
		loading = load(rec);
	}, { immediate: true });

	async function start(partId: string): Promise<boolean> {
		if (recording.value) return false;
		micAttempt += 1;
		const current = micAttempt;
		startError.value = null;
		micPart = partId;
		recording.value = true;
		let started: boolean;
		try {
			// False when a cancel (its recorder.stop()) or a newer start came while the
			// microphone opened: the recorder then released that stream itself.
			started = await recorder.start();
		}
		catch (err) {
			if (current === micAttempt) {
				recording.value = false;
				micPart = null;
				startError.value = errorText(err);
			}
			return false;
		}
		if (!started) {
			if (current === micAttempt) {
				recording.value = false;
				micPart = null;
			}
			return false;
		}
		// The recorder runs for this start (nothing came in between), so stopping it
		// here can only close this attempt's microphone.
		if (current !== micAttempt) {
			await recorder.stop();
			const session = recorder.sessionId.value;
			if (session) void discardUnfinished(session);
			return false;
		}
		return true;
	}

	async function cancel(): Promise<void> {
		if (!recording.value) return;
		micAttempt += 1;
		recording.value = false;
		micPart = null;
		await recorder.stop();
		const session = recorder.sessionId.value;
		if (session) void discardUnfinished(session);
	}

	async function finish(): Promise<boolean> {
		const id = micPart;
		if (!recording.value || id === null) return false;
		micAttempt += 1;
		micPart = null;
		// After stop(): the native recorder names its session (its file) only then.
		const blob = await recorder.stop();
		const session = recorder.sessionId.value;
		recording.value = false;
		if (blob.size === 0) {
			if (session) void discardUnfinished(session);
			startError.value = "Die Aufnahme ist leer.";
			return false;
		}
		if (session) sessions.set(id, session);
		blobs.set(id, blob);
		parts.value = [...parts.value, { id, stored: "saving", status: "transcribing", error: null, audio: "appending", audioError: null }];
		// Counted from here, so leaving while the anchor is computed still asks.
		creating.value += 1;
		const created = (async () => {
			try {
				await loading;
				let anchor: RedictationAnchor;
				try {
					anchor = await hooks.anchor(id);
				}
				catch {
					anchor = { anchor_offset: null, anchor_text_sha: null };
				}
				inputs.set(id, { seq: ++maxSeq, anchor });
				await createRow(id);
			}
			finally {
				creating.value -= 1;
			}
		})();
		creations.set(id, created);
		queueAudio(id, async () => blobs.get(id) ?? null);
		return true;
	}

	function retry(partId: string): void {
		const part = find(partId);
		if (part?.status !== "error") return;
		if (part.stored === "failed") {
			void createRow(partId);
			return;
		}
		setPart(partId, { status: "transcribing", error: null });
		void (async () => {
			try {
				await store.updatePart(partId, { status: "pending" });
				void pollLoop();
			}
			catch (err) {
				const message = `Erneuter Versuch fehlgeschlagen: ${errorText(err)}`;
				setPart(partId, { status: "error", error: message });
				hooks.onError(partId, message);
			}
		})();
	}

	function retryAudio(partId: string): void {
		const blob = blobs.get(partId);
		if (find(partId)?.audio !== "failed" || !blob) return;
		queueAudio(partId, async () => blob);
	}

	function discard(partId: string): void {
		const part = find(partId);
		if (part?.status !== "error" && part?.audio !== "failed") return;
		if (part.stored === "saved" && part.status === "error") {
			void store.updatePart(partId, { status: "discarded" }).catch(() => {});
		}
		forget(partId);
		dropSession(partId);
	}

	function saved(): void {
		for (const id of [...unapplied]) void markApplied(id);
	}

	function reanchor(next: Map<string, RedictationAnchor>): void {
		for (const [id, anchor] of next) {
			if (find(id)?.stored !== "saved") continue;
			const prev = anchors.get(id);
			if (prev?.anchor_offset === anchor.anchor_offset && prev?.anchor_text_sha === anchor.anchor_text_sha) continue;
			anchors.set(id, anchor);
			void store.updatePart(id, anchor).catch(() => {
				// The next save tries again.
				anchors.delete(id);
			});
		}
	}

	onScopeDispose(() => {
		disposed = true;
		for (const wake of [...wakers]) wake();
		if (recording.value) void cancel();
		if (typeof window !== "undefined") window.removeEventListener("beforeunload", onBeforeUnload);
	});

	return {
		recorder,
		recording,
		startError,
		parts,
		transcribing,
		merging,
		busy,
		failed,
		start,
		cancel,
		finish,
		retry,
		retryAudio,
		discard,
		saved,
		reanchor,
	};
}
