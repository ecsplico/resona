import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useDeviceSpeech, bcp47 } from "~/composables/useDeviceSpeech";

type Availability = "unavailable" | "downloadable" | "downloading" | "available";

interface FakeResult { isFinal: boolean; 0: { transcript: string }; length: number }

class FakeRecognition {
	static availableResult: Availability = "available";
	static available = vi.fn(async (_opts: unknown) => FakeRecognition.availableResult);
	static install = vi.fn(async (_opts: unknown) => true);
	static instances: FakeRecognition[] = [];

	lang = "";
	continuous = false;
	interimResults = false;
	processLocally = false;
	onresult: ((e: { resultIndex: number; results: FakeResult[] }) => void) | null = null;
	onerror: ((e: { error: string }) => void) | null = null;
	onend: (() => void) | null = null;
	start = vi.fn();
	stop = vi.fn();

	constructor() {
		FakeRecognition.instances.push(this);
	}

	emit(resultIndex: number, results: Array<[string, boolean]>): void {
		this.onresult?.({
			resultIndex,
			results: results.map(([transcript, isFinal]) => ({ isFinal, 0: { transcript }, length: 1 })),
		});
	}
}

const g = globalThis as Record<string, unknown>;

function install(ctor: unknown): void {
	g.SpeechRecognition = ctor;
}

describe("useDeviceSpeech", () => {
	beforeEach(() => {
		FakeRecognition.availableResult = "available";
		FakeRecognition.available.mockClear();
		FakeRecognition.install.mockClear();
		FakeRecognition.instances = [];
		install(FakeRecognition);
	});

	afterEach(() => {
		delete g.SpeechRecognition;
		delete g.webkitSpeechRecognition;
	});

	it("maps languages to BCP 47", () => {
		expect(bcp47("de")).toBe("de-DE");
		expect(bcp47("en")).toBe("en-US");
		expect(bcp47("fr-CA")).toBe("fr-CA");
	});

	it("reports unsupported without a recognizer or without static available()", async () => {
		delete g.SpeechRecognition;
		expect(await useDeviceSpeech().availability("de")).toBe("unsupported");
		// A recognizer constructor without the static `available()` method.
		g.webkitSpeechRecognition = function LegacyRecognition() {};
		expect(await useDeviceSpeech().availability("de")).toBe("unsupported");
	});

	it("asks for local-only availability with the BCP 47 tag", async () => {
		FakeRecognition.availableResult = "downloadable";
		expect(await useDeviceSpeech().availability("de")).toBe("downloadable");
		expect(FakeRecognition.available).toHaveBeenCalledWith({ langs: ["de-DE"], processLocally: true });
	});

	it("install passes local-only options", async () => {
		expect(await useDeviceSpeech().install("en")).toBe(true);
		expect(FakeRecognition.install).toHaveBeenCalledWith({ langs: ["en-US"], processLocally: true });
	});

	it("start throws unless the language is available locally", async () => {
		FakeRecognition.availableResult = "downloadable";
		const speech = useDeviceSpeech();
		await expect(speech.start({ language: "de" })).rejects.toThrow();
		expect(FakeRecognition.instances).toHaveLength(0);
		expect(speech.status.value).toBe("CLOSED");
	});

	it("starts a continuous, interim, local-only recognizer", async () => {
		const speech = useDeviceSpeech();
		await speech.start({ language: "de" });
		const rec = FakeRecognition.instances[0]!;
		expect(rec.lang).toBe("de-DE");
		expect(rec.continuous).toBe(true);
		expect(rec.interimResults).toBe(true);
		expect(rec.processLocally).toBe(true);
		expect(rec.start).toHaveBeenCalledTimes(1);
		expect(speech.status.value).toBe("OPEN");
	});

	it("appends finals from resultIndex, fires onFinal and sets partial from interims", async () => {
		const speech = useDeviceSpeech();
		const finals: string[] = [];
		const off = speech.onFinal(t => finals.push(t));
		await speech.start({ language: "de" });
		const rec = FakeRecognition.instances[0]!;

		rec.emit(0, [["hallo", false]]);
		expect(speech.partial.value).toBe("hallo");
		expect(speech.final.value).toBe("");

		rec.emit(0, [["hallo welt", true]]);
		expect(speech.final.value).toBe("hallo welt");
		expect(speech.partial.value).toBe("");

		// Earlier results before resultIndex must not be appended again.
		rec.emit(1, [["hallo welt", true], ["wie geht", true], ["es", false]]);
		expect(speech.final.value).toBe("hallo welt wie geht");
		expect(speech.partial.value).toBe("es");
		expect(speech.fullText.value).toBe("hallo welt wie geht es");
		expect(finals).toEqual(["hallo welt", "wie geht"]);

		off();
		rec.emit(3, [["", false], ["", false], ["", false], ["ende", true]]);
		expect(finals).toEqual(["hallo welt", "wie geht"]);
		expect(speech.final.value).toBe("hallo welt wie geht ende");
	});

	it("restarts on end while running, not after stop", async () => {
		const speech = useDeviceSpeech();
		await speech.start({ language: "de" });
		const rec = FakeRecognition.instances[0]!;
		rec.onend?.();
		expect(rec.start).toHaveBeenCalledTimes(2);

		speech.stop();
		expect(rec.stop).toHaveBeenCalled();
		expect(speech.status.value).toBe("CLOSED");
		rec.onend?.();
		expect(rec.start).toHaveBeenCalledTimes(2);
	});

	it("ignores no-speech", async () => {
		const speech = useDeviceSpeech();
		await speech.start({ language: "de" });
		FakeRecognition.instances[0]!.onerror?.({ error: "no-speech" });
		expect(speech.error.value).toBeNull();
		expect(speech.status.value).toBe("OPEN");
	});

	it.each([
		["not-allowed", "Mikrofon nicht erlaubt"],
		["service-not-allowed", "Lokale Spracherkennung nicht erlaubt"],
		["language-not-supported", "Sprache lokal nicht verfügbar"],
		["audio-capture", "Kein Mikrofon"],
	])("maps %s to its message and stops", async (code, message) => {
		const speech = useDeviceSpeech();
		await speech.start({ language: "de" });
		const rec = FakeRecognition.instances[0]!;
		rec.onerror?.({ error: code });
		expect(speech.error.value).toBe(message);
		expect(speech.status.value).toBe("CLOSED");
		expect(rec.stop).toHaveBeenCalled();
		rec.onend?.();
		expect(rec.start).toHaveBeenCalledTimes(1);
	});

	it("clears a previous error on the next start", async () => {
		const speech = useDeviceSpeech();
		await speech.start({ language: "de" });
		FakeRecognition.instances[0]!.onerror?.({ error: "audio-capture" });
		await speech.start({ language: "de" });
		expect(speech.error.value).toBeNull();
		expect(speech.status.value).toBe("OPEN");
	});
});
