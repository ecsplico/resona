import { afterEach, describe, expect, it, vi } from "vitest";
import { acceptRefinedTitle, refineTitle, titleModelAvailability } from "~/utils/titleRefinement";

const TEXT = "Kontrolle nach Knie-TEP rechts, Wunde reizlos, Beweglichkeit 0-0-90 Grad, Patient mobil an Unterarmgehstützen.";

function stubModel(state: string, answer: () => Promise<string> = async () => "Knie-TEP Kontrolle rechts") {
	const session = { prompt: vi.fn(answer), destroy: vi.fn() };
	const model = {
		availability: vi.fn(async () => state),
		create: vi.fn(async () => session),
	};
	vi.stubGlobal("LanguageModel", model);
	return { model, session };
}

afterEach(() => {
	vi.unstubAllGlobals();
});

describe("acceptRefinedTitle", () => {
	it.each([
		["Knie-TEP Kontrolle rechts", "Knie-TEP Kontrolle rechts"],
		["  „Knie-TEP Kontrolle rechts.“ ", "Knie-TEP Kontrolle rechts"],
		["Titel: Wundkontrolle Unterschenkel links", "Wundkontrolle Unterschenkel links"],
		["verbandswechsel nach OP", "Verbandswechsel nach OP"],
	])("keeps a usable title %j", (raw, expected) => {
		expect(acceptRefinedTitle(raw)).toBe(expected);
	});

	it.each([
		["empty", "   "],
		["several lines", "Knie-TEP Kontrolle\nHier ist Ihr Titel"],
		["too long", "Kontrolle der Wundheilung nach Implantation einer Knietotalendoprothese rechts bei Arthrose"],
		["too many words", "Kontrolle der Wunde am Knie nach der OP vor zwei Wochen"],
		["cut off with ellipsis", "Kontrolle Knie-TEP rechts …"],
		["cut off with comma", "Kontrolle Knie-TEP rechts,"],
		["unclosed bracket", "Kontrolle Knie-TEP (rechts"],
		["English", "Follow-up of the right knee"],
		["other script", "Контроль колена"],
	])("rejects %s", (_name, raw) => {
		expect(acceptRefinedTitle(raw)).toBeNull();
	});
});

describe("titleModelAvailability", () => {
	it("is unsupported without the Prompt API (the test environment)", async () => {
		expect(await titleModelAvailability("de")).toBe("unsupported");
	});

	it("is unsupported for languages other than German", async () => {
		const { model } = stubModel("available");
		expect(await titleModelAvailability("en")).toBe("unsupported");
		expect(model.availability).not.toHaveBeenCalled();
	});

	it("reports the model state for German", async () => {
		const { model } = stubModel("downloadable");
		expect(await titleModelAvailability("de")).toBe("downloadable");
		expect(model.availability).toHaveBeenCalledWith({
			expectedInputs: [{ type: "text", languages: ["de"] }],
			expectedOutputs: [{ type: "text", languages: ["de"] }],
		});
	});
});

describe("refineTitle", () => {
	it("returns null without the Prompt API", async () => {
		expect(await refineTitle(TEXT, { language: "de", signal: new AbortController().signal })).toBeNull();
	});

	it.each(["downloadable", "downloading", "unavailable"])("never creates a session (no download) when %s", async (state) => {
		const { model } = stubModel(state);
		expect(await refineTitle(TEXT, { language: "de", signal: new AbortController().signal })).toBeNull();
		expect(model.create).not.toHaveBeenCalled();
	});

	it("formulates a title when the model is available and frees the session", async () => {
		const { model, session } = stubModel("available");
		const signal = new AbortController().signal;
		expect(await refineTitle(TEXT, { language: "de", signal })).toBe("Knie-TEP Kontrolle rechts");
		expect(model.create).toHaveBeenCalledWith(expect.objectContaining({ signal }));
		expect(session.prompt).toHaveBeenCalledWith(expect.stringContaining(TEXT), { signal });
		expect(session.destroy).toHaveBeenCalled();
	});

	it("returns null for an unusable answer", async () => {
		stubModel("available", async () => "The right knee follow-up");
		expect(await refineTitle(TEXT, { language: "de", signal: new AbortController().signal })).toBeNull();
	});

	it("returns null when the prompt fails", async () => {
		const { session } = stubModel("available", async () => {
			throw new Error("model crashed");
		});
		expect(await refineTitle(TEXT, { language: "de", signal: new AbortController().signal })).toBeNull();
		expect(session.destroy).toHaveBeenCalled();
	});

	it("drops an answer that arrives after the abort", async () => {
		const run = new AbortController();
		const answer = Promise.withResolvers<string>();
		const { session } = stubModel("available", () => answer.promise);
		const pending = refineTitle(TEXT, { language: "de", signal: run.signal });
		await vi.waitFor(() => expect(session.prompt).toHaveBeenCalled());
		run.abort();
		answer.resolve("Knie-TEP Kontrolle rechts");
		expect(await pending).toBeNull();
	});
});
